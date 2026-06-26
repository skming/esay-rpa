"""
抓取 https://rss-test.yingdiantone.com 申请列表 /invest_apply
用法: python tools/scrape_invest_apply.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BASE_URL   = "https://rss-test.yingdiantone.com"
USERNAME   = "admin"
PASSWORD   = "123456"
CAPTCHA    = "123456"
OUTPUT     = Path(__file__).parent / "invest_apply_result.json"


async def run() -> None:
    from playwright.async_api import async_playwright, Request, Response

    api_calls: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx     = await browser.new_context(ignore_https_errors=True)
        page    = await ctx.new_page()

        # ── 拦截所有 XHR / fetch 响应，记录 API 调用 ──────────────
        async def on_response(resp: Response) -> None:
            req = resp.request
            if req.resource_type not in {"xhr", "fetch"}:
                return
            try:
                body = await resp.json()
            except Exception:
                body = None
            api_calls.append({
                "method": req.method,
                "url":    req.url,
                "status": resp.status,
                "body":   body,
            })

        page.on("response", on_response)

        # ── 1. 打开站点，等待登录页出现 ───────────────────────────
        print(f"[1/5] 打开 {BASE_URL} …")
        await page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1500)

        print(f"[2/5] 当前 URL: {page.url}")
        print(f"      页面标题: {await page.title()}")

        # ── 2. 找到并填写登录表单 ─────────────────────────────────
        # 尝试常见选择器
        selectors_user = ["input[placeholder*='用户']", "input[name='username']",
                          "input[type='text']", "#username", ".el-input input"]
        selectors_pass = ["input[placeholder*='密码']", "input[name='password']",
                          "input[type='password']", "#password"]
        selectors_cap  = ["input[placeholder*='验证']", "input[name='captcha']",
                          "input[name='code']", "input[placeholder*='码']"]

        async def fill_first(selectors: list[str], value: str) -> bool:
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.fill(value)
                        return True
                except Exception:
                    pass
            return False

        print("[3/5] 填写登录表单 …")
        found_user = await fill_first(selectors_user, USERNAME)
        found_pass = await fill_first(selectors_pass, PASSWORD)
        found_cap  = await fill_first(selectors_cap,  CAPTCHA)
        print(f"      用户名: {'✓' if found_user else '✗'}  密码: {'✓' if found_pass else '✗'}  验证码: {'✓' if found_cap else '✗'}")

        # ── 3. 提交登录 ───────────────────────────────────────────
        submit_selectors = [
            "button[type='submit']", ".el-button--primary", "button:has-text('登录')",
            "input[type='submit']"
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    submitted = True
                    print(f"      点击提交按钮: {sel}")
                    break
            except Exception:
                pass

        if not submitted:
            print("      未找到提交按钮，尝试回车提交 …")
            await page.keyboard.press("Enter")

        # 等待登录跳转
        await page.wait_for_timeout(3000)
        print(f"      登录后 URL: {page.url}")

        # ── 4. 导航到 /invest_apply ───────────────────────────────
        target = f"{BASE_URL}/#/invest_apply"
        print(f"[4/5] 导航到 {target} …")
        await page.goto(target, wait_until="networkidle", timeout=20_000)
        await page.wait_for_timeout(2500)
        print(f"      当前 URL: {page.url}")

        # 截图留证
        screenshot = Path(__file__).parent / "invest_apply_screenshot.png"
        await page.screenshot(path=str(screenshot), full_page=True)
        print(f"      截图已保存: {screenshot}")

        # ── 5. 提取页面数据 ──────────────────────────────────────
        print("[5/5] 提取数据 …")

        # 方法 A：从拦截的 API 响应里找申请列表
        invest_api = [c for c in api_calls if "invest" in c["url"].lower() or "apply" in c["url"].lower()]

        # 方法 B：从 DOM 提取表格数据
        table_data: list[dict] = []
        try:
            rows = await page.locator("table tbody tr, .el-table__body tr").all()
            for row in rows:
                cells = await row.locator("td").all_text_contents()
                if cells:
                    table_data.append({"cells": cells})
        except Exception as e:
            print(f"      DOM 表格提取失败: {e}")

        # ── 输出结果 ─────────────────────────────────────────────
        result = {
            "url":         page.url,
            "title":       await page.title(),
            "api_calls":   api_calls,
            "invest_api":  invest_api,
            "table_rows":  table_data,
        }

        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n✓ 结果已保存: {OUTPUT}")
        print(f"  API 调用总数: {len(api_calls)}")
        print(f"  申请相关 API: {len(invest_api)}")
        print(f"  表格行数: {len(table_data)}")

        if invest_api:
            print("\n── 申请列表 API 响应 ──")
            for call in invest_api[:3]:
                print(f"  {call['method']} {call['url']}  [{call['status']}]")
                if call["body"]:
                    print(f"  {json.dumps(call['body'], ensure_ascii=False)[:300]}")

        if table_data:
            print("\n── 表格前 5 行 ──")
            for row in table_data[:5]:
                print(f"  {row['cells']}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
