"""UI acceptance against a running localhost Agent. Uses actual Qwen planning."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent.parent


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1080},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:8765')
        page.wait_for_function("document.getElementById('score').textContent !== '—'")
        assert page.locator('#dataset-count').inner_text()=='20 条'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(ROOT/'screenshots/06-agent-home.png'),full_page=True)
        page.get_by_role('button',name='解释一次评分',exact=False).click()
        page.locator('#send').wait_for(state='visible')
        page.wait_for_function("!document.getElementById('send').disabled",timeout=180000)
        assert page.locator('.case-chip').count()>0
        assert 'case_16' in page.locator('#case-panel').inner_text()
        assert page.locator('.trace-item').count()>0
        page.locator('.trace summary').first.click()
        page.screenshot(path=str(ROOT/'screenshots/07-agent-tools.png'),full_page=True)
        page.locator('#message').fill('请打开 case_08 的证据表单。')
        page.locator('#send').click()
        page.wait_for_function("!document.getElementById('send').disabled",timeout=180000)
        page.locator('#evidence-dialog').wait_for(state='visible')
        page.screenshot(path=str(ROOT/'screenshots/08-agent-evidence.png'),full_page=True)
        page.locator('#evidence-status').select_option('supported')
        page.locator('#evidence-source').fill('仅用于自动化验收的构造来源，不是真实商品证据')
        page.locator('#evidence-reason').fill('验证证据表单、会话隔离与重算行为；不作为正式评估结论。')
        page.get_by_role('button',name='保存并重新计算').click()
        page.locator('#evidence-dialog').wait_for(state='hidden')
        assert '按你提供的来源重新计算' in page.locator('#messages').inner_text()
        assert page.locator('#current-report').get_attribute('href') is None
        # Invalid upload is rejected; valid new input clears old scores.
        page.locator('#file').set_input_files({'name':'bad.json','mimeType':'application/json','buffer':b'{}'})
        page.wait_for_function("document.getElementById('toast').textContent.includes('JSON 数组')")
        new=json.dumps([{'id':'uploaded_01','user_question':'如何查询？','auto_reply':'请先告诉我需要查询什么。'}],ensure_ascii=False).encode()
        page.locator('#file').set_input_files({'name':'new.json','mimeType':'application/json','buffer':new})
        page.wait_for_function("document.getElementById('dataset-count').textContent === '1 条'")
        assert page.locator('#score').inner_text()=='—'
        assert page.locator('#current-report').get_attribute('href') is None
        page.locator('#reset').click()
        page.wait_for_function("document.getElementById('dataset-count').textContent === '20 条'")
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(ROOT/'screenshots/09-agent-mobile.png'),full_page=True)
        assert not errors, errors
        browser.close()
    report={'mode':'real_agent_browser_test','passed':True,'checks':['initial dataset and v3 score','desktop layout','real natural-language case explanation','tool trace','case evidence','request evidence dialog','session-only evidence recalculation','evidence invalidates old report link','invalid upload rejection','new dataset clears old scores and report link','reset','mobile layout','no JS errors']}
    (ROOT/'docs/agent-browser-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
