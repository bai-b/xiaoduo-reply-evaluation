"""Optional visual QA. Requires playwright; core evaluation does not."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / 'screenshots'


def main():
    SCREENSHOTS.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width':1440, 'height':1080}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto((ROOT/'output/index.html').as_uri())
        page.locator('.case').last.wait_for()
        assert page.locator('.case').count() == 20
        assert '真实 LLM' in page.locator('#mode').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(SCREENSHOTS/'01-overview.png'), full_page=False)
        page.locator('#search').fill('case_11')
        assert page.locator('.case').count() == 1
        page.locator('.case summary').click()
        assert page.locator('.case').get_attribute('open') is not None
        page.locator('.case').screenshot(path=str(SCREENSHOTS/'02-case-evidence.png'))
        page.locator('#search').fill('no-such-case')
        assert page.locator('.case').count() == 0
        assert page.locator('.empty').is_visible()
        page.locator('#search').fill('')
        page.locator('#filter').select_option('risk')
        assert page.locator('.case').count() > 0
        assert all('高风险待复核' in text for text in page.locator('.case summary').all_text_contents())
        page.locator('#filter').select_option('all')
        page.locator('#sort').select_option('score')
        assert page.locator('.case').first.get_attribute('data-id') == 'case_11'
        page.locator('[data-tab="calibration"]').click()
        assert page.locator('#calibration').is_visible()
        page.locator('#calibration').screenshot(path=str(SCREENSHOTS/'03-calibration.png'))
        page.locator('[data-tab="method"]').click()
        assert page.locator('#method').is_visible()
        page.locator('#method').screenshot(path=str(SCREENSHOTS/'04-method.png'))
        page.set_viewport_size({'width':390, 'height':844})
        page.locator('[data-tab="cases"]').click()
        page.evaluate('window.scrollTo(0,0)')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(SCREENSHOTS/'05-mobile.png'), full_page=False)
        assert not errors, errors
        browser.close()
    report = {'browser': 'Microsoft Edge / Playwright', 'checks': ['20 cases render', 'real mode visible', 'desktop no horizontal overflow', 'search', 'case expansion', 'empty state', 'risk filter', 'score sort', 'calibration tab', 'method tab', 'mobile no horizontal overflow', 'no JS errors'], 'passed': True}
    (ROOT/'docs/browser-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
