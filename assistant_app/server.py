from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from run import ROOT, load_env
from .agent import QualityAgent, new_session, apply_evidence

REPORT_FILES = {'index.html','report.md','results.json','scores.csv','validation.json','issue_validation.json','comparison.md'}


def validate_upload(rows):
    if not isinstance(rows,list) or not 1 <= len(rows) <= 100:
        raise ValueError('数据必须是1至100条记录的 JSON 数组')
    seen = set()
    clean = []
    for r in rows:
        if not isinstance(r,dict) or any(not isinstance(r.get(k),str) or not r[k].strip() for k in ('id','user_question','auto_reply')):
            raise ValueError('每条记录需要非空 id、user_question、auto_reply')
        if r['id'] in seen or len(r['id'])>100 or len(r['user_question'])>6000 or len(r['auto_reply'])>12000:
            raise ValueError('id 重复或文本超出长度限制')
        seen.add(r['id'])
        clean.append({k:r[k] for k in ('id','user_question','auto_reply')})
    return clean


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self,address, mode='llm'):
        super().__init__(address, Handler)
        self.sessions = {}
        self.jobs = {}
        self.csrf = secrets.token_urlsafe(32)
        self.mode = mode


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # No chat, API credentials or uploaded data in access logs.

    def send_json(self,payload,status=200):
        raw=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def allowed_host(self):
        return self.headers.get('Host','') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def session(self, sid):
        if not isinstance(sid,str) or sid not in self.server.sessions:
            raise ValueError('会话不存在，请刷新页面')
        return self.server.sessions[sid]

    def do_GET(self):
        if not self.allowed_host():
            return self.send_json({'error':'只接受本机 Host'},403)
        path=urlparse(self.path).path
        query=parse_qs(urlparse(self.path).query)
        try:
            if path=='/api/bootstrap':
                sid=query.get('session',[''])[0]
                s=self.server.sessions.get(sid)
                if s is None:
                    s=new_session()
                    self.server.sessions[s.id]=s
                return self.send_json({'session_id':s.id,'csrf':self.server.csrf,'dataset':s.dataset_name,'count':len(s.rows),
                        'overview':s.result['summary'] if s.result else None,'mode':self.server.mode,'history':s.history,
                        'report_url':s.report_url,
                        'model':s.result['metadata']['model'] if s.result else '使用本机配置的千问模型'})
            if path=='/api/job':
                job=self.server.jobs.get(query.get('id',[''])[0])
                if not job: return self.send_json({'error':'任务不存在'},404)
                return self.send_json(dict(job))
            if path=='/api/case':
                s=self.session(query.get('session',[''])[0])
                r=next((r for r in (s.result or {}).get('results',[]) if r['id']==query.get('id',[''])[0]),None)
                if not r: return self.send_json({'error':'当前数据没有该案例或尚未评估'},404)
                return self.send_json(r)
            if path=='/':
                return self.send_file(ROOT/'assistant_app/index.html','text/html; charset=utf-8')
            if path.startswith('/reports/'):
                pieces=path.split('/')
                if len(pieces)!=4 or pieces[3] not in REPORT_FILES:
                    return self.send_json({'error':'文件不可访问'},404)
                sid=pieces[2]
                if sid=='latest':
                    folder=ROOT/'output'
                elif sid in self.server.sessions:
                    folder=ROOT/'.cache/sessions'/sid/'report'
                else:
                    return self.send_json({'error':'报告不存在'},404)
                target=folder/pieces[3]
                if not target.is_file(): return self.send_json({'error':'报告尚未生成'},404)
                return self.send_file(target)
            self.send_json({'error':'不存在的页面'},404)
        except ValueError as e:
            self.send_json({'error':str(e)},400)

    def send_file(self,path, content_type=None):
        raw=path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',content_type or ((mimetypes.guess_type(str(path))[0] or 'application/octet-stream')+'; charset=utf-8'))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        allowed_origins={f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.allowed_host() or self.headers.get('Origin') not in allowed_origins | {None} or self.headers.get('X-Session-Token') != self.server.csrf:
            return self.send_json({'error':'请求来源或会话令牌无效'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2_000_000: raise ValueError('请求为空或超过2MB')
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict): raise ValueError('请求必须是JSON对象')
            s=self.session(body.get('session_id'))
            path=urlparse(self.path).path
            if path=='/api/chat':
                message=body.get('message')
                if not isinstance(message,str) or not 1<=len(message.strip())<=6000:
                    raise ValueError('请提供1至6000字的任务描述')
                if not s.lock.acquire(blocking=False): return self.send_json({'error':'本会话正在处理任务，请等待完成'},409)
                jid=uuid.uuid4().hex
                job={'status':'running','events':[],'result':None,'error':None}
                self.server.jobs[jid]=job
                def work():
                    try:
                        agent=QualityAgent(s,mode=self.server.mode)
                        job['result']=agent.run(message,lambda event:job['events'].append(event))
                        job['status']='done'
                    except Exception as e:
                        job['error']=str(e) if isinstance(e,(ValueError,OSError)) else '任务遇到内部错误，请检查本地服务；未生成虚假结果。'
                        job['status']='error'
                    finally:
                        s.lock.release()
                threading.Thread(target=work,daemon=True).start()
                return self.send_json({'job_id':jid},202)
            if not s.lock.acquire(blocking=False): return self.send_json({'error':'请等待当前任务结束'},409)
            try:
                if path=='/api/upload':
                    rows=validate_upload(body.get('rows'))
                    name=body.get('name','导入的数据集')
                    if not isinstance(name,str) or len(name)>150: raise ValueError('文件名无效')
                    s.rows=rows;s.dataset_name=name;s.result=None;s.builtin=False;s.evidence={};s.history=[];s.report_url=None
                    return self.send_json({'count':len(rows),'dataset':name,'message':'已导入并清空旧数据集上下文，请开始评估。'})
                if path=='/api/reset':
                    fresh=new_session()
                    s.rows=fresh.rows;s.dataset_name=fresh.dataset_name;s.result=fresh.result;s.builtin=True;s.evidence={};s.history=[];s.report_url=fresh.report_url
                    return self.send_json({'count':len(s.rows),'dataset':s.dataset_name,'overview':s.result['summary'] if s.result else None,'report_url':s.report_url})
                if path=='/api/evidence':
                    return self.send_json(apply_evidence(s,body.get('evidence')))
                return self.send_json({'error':'不存在的操作'},404)
            finally:
                s.lock.release()
        except (ValueError,TypeError,UnicodeDecodeError) as e:
            self.send_json({'error':str(e)},400)


def main():
    load_env()
    parser=argparse.ArgumentParser(description='REPLY LAB 本地客服质量 Agent')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--evaluation-mode',choices=['llm','mock'],default='llm',help='Agent调度仍使用真实LLM；只控制新数据评分方式')
    args=parser.parse_args()
    server=AppServer(('127.0.0.1',args.port),args.evaluation_mode)
    print(f'REPLY LAB Agent: http://127.0.0.1:{server.server_port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
