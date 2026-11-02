# -*- coding: utf-8 -*-
"""河北环境工程学院教务课表 -> WakeUp 课程表 CSV 导出工具。

流程：
    1. 脚本打开学校 WebVPN。
    2. 你手动登录统一身份认证，进入教务系统，打开「个人课表查询」并选好学期。
    3. 回到终端按回车，脚本抓取当前页面里的课表并生成 CSV。

课表数据只在本机处理，不上传任何服务器。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / ".auth" / "state.json"
OUTPUT_DIR = BASE_DIR / "output"

WEBVPN_URL = "https://webvpn.hebuee.edu.cn/"

# 当前课表页面同目录下的正方接口。用相对路径，兼容 WebVPN 重写后的地址。
SCHEDULE_ENDPOINTS = [
    "xskbcx_cxXsgrkb.html?gnmkdm=N2151",
    "xskbcx_cxXsKb.html?gnmkdm=N2151",
]

CSV_HEADER = ["课程名称", "星期", "开始节数", "结束节数", "老师", "地点", "周数"]

if hasattr(sys.stdout, "reconfigure"):
    # run.bat 已把控制台切到 UTF-8；同时避免个别字符导致 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class Course:
    name: str
    day: int
    start: int
    end: int
    teacher: str
    room: str
    weeks_text: str


def log(msg: str = "") -> None:
    print(msg, flush=True)


def clean_room(raw: str, shorten: bool = True) -> str:
    """去掉校区前缀，缩短过长的楼名，避免 WakeUp 小组件显示不全。"""
    text = (raw or "").strip()
    if not text:
        return "无"
    if shorten:
        text = re.sub(r"^(主校区|校本部|分校区|校区)\s*", "", text)
        text = text.replace("综合教学楼", "综合楼")
    return text or "无"


def format_weeks(raw: str) -> str:
    """把正方 zcd 转成 WakeUp 的周数写法。

    例：
        1-5周,7-11周(单),12-16周(双) -> 1-5、7-11单、12-16双
        1,3,5,7周 -> 1、3、5、7
    """
    text = str(raw or "").strip()
    if not text:
        return "1-16"

    # 直接提取“区间 + 可选单双”，兼容逗号、顿号、空格甚至无分隔符的写法
    pattern = re.compile(r"(\d+)\s*(?:[-~至到]\s*(\d+))?\s*周?\s*[\(（]?\s*([单双])?\s*[\)）]?")
    matches = list(pattern.finditer(text))
    if not matches:
        return "1-16"

    result: list[str] = []
    for match in matches:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        parity = match.group(3) or ""
        if start > end:
            start, end = end, start

        if start == end:
            # 单个周次本身就是具体周，不需要再标单双
            result.append(str(start))
        elif parity:
            result.append(f"{start}-{end}{parity}")
        else:
            result.append(f"{start}-{end}")

    return "、".join(result) if result else "1-16"


def parse_sections(raw) -> tuple[int, int]:
    """解析节次：1-2节 / 3-5节 / 1,2节 / 单节。"""
    text = str(raw or "").strip().replace("节", "")
    nums = [int(n) for n in re.findall(r"\d+", text)]
    if not nums:
        return 1, 1
    return min(nums), max(nums)


def parse_course(item: dict, shorten_room: bool = True) -> Course | None:
    name = (item.get("kcmc") or item.get("courseName") or item.get("kcmc_name") or "").strip()
    if not name:
        return None

    day_raw = item.get("xqj") or item.get("xq") or item.get("day") or 0
    day = int(re.sub(r"\D", "", str(day_raw)) or 0)
    if day < 1 or day > 7:
        return None

    if item.get("jc"):
        start, end = parse_sections(item["jc"])
    else:
        start_raw = item.get("jcs") or item.get("startNode") or item.get("start") or 1
        end_raw = item.get("jce") or item.get("endNode") or item.get("end") or start_raw
        start = int(re.sub(r"\D", "", str(start_raw)) or 1)
        end = int(re.sub(r"\D", "", str(end_raw)) or start)

    teacher = (item.get("xm") or item.get("teaxms") or item.get("teacher") or "").strip() or "无"
    room = clean_room(item.get("cdmc") or item.get("jxcdmc") or item.get("room") or "", shorten_room)
    weeks = format_weeks(item.get("zcd") or item.get("zcmc") or item.get("weeks") or "")

    return Course(name=name, day=day, start=start, end=end, teacher=teacher, room=room, weeks_text=weeks)


def extract_course_list(data) -> list[dict] | None:
    """从正方接口的各种返回结构中找出课程数组。"""
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and ("kcmc" in data[0] or "courseName" in data[0]):
            return data
        return None
    if not isinstance(data, dict):
        return None

    for key in ("kbList", "sjkList", "courseList", "list"):
        value = data.get(key)
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            if value:
                return value

    for value in data.values():
        found = extract_course_list(value)
        if found:
            return found
    return None


def courses_from_items(items: list[dict], shorten_room: bool = True) -> list[Course]:
    courses = []
    for item in items:
        try:
            course = parse_course(item, shorten_room)
        except Exception:
            continue
        if course:
            courses.append(course)

    unique: dict[tuple, Course] = {}
    for course in courses:
        key = (course.name, course.day, course.start, course.end, course.teacher, course.room, course.weeks_text)
        unique[key] = course

    return sorted(unique.values(), key=lambda c: (c.day, c.start, c.name))


def write_csv(courses: list[Course], term: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_term = re.sub(r'[\\/:*?"<>|]', "_", term)
    path = OUTPUT_DIR / f"WakeUp课表-{safe_term}.csv"

    # utf-8-sig 让 Excel / WPS 直接双击打开也不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for course in courses:
            writer.writerow([course.name, course.day, course.start, course.end, course.teacher, course.room, course.weeks_text])

    return path


def guess_term() -> tuple[str, str]:
    """页面读不到学期时，按当前日期推测。"""
    now = time.localtime()
    year, month = now.tm_year, now.tm_mon
    if month >= 9:
        return str(year), "3"
    if month == 1:
        return str(year - 1), "3"
    return str(year - 1), "12"


def term_label(xnm: str, xqm: str) -> str:
    try:
        start = int(xnm)
    except Exception:
        return time.strftime("%Y-%m-%d")
    term = {"3": "第一学期", "12": "第二学期", "16": "短学期"}.get(str(xqm), f"学期{xqm}")
    return f"{start}-{start + 1}学年{term}"


async def read_term(frame) -> tuple[str | None, str | None, str | None]:
    """读取某个页面/iframe 里当前选中的学年、学期。"""
    try:
        info = await frame.evaluate(
            """() => {
                const pick = (id) => {
                    const el = document.querySelector(id);
                    if (!el) return null;
                    return {
                        value: el.value,
                        text: el.options && el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ''
                    };
                };
                const body = document.body ? document.body.innerText : '';
                const m = body.match(/(\\d{4})-(\\d{4})\\s*学年\\s*(第[一二]学期|短学期)/);
                return {
                    xnm: pick('#xnm'),
                    xqm: pick('#xqm'),
                    termText: m ? m[0].replace(/\\s+/g, '') : null
                };
            }"""
        )
    except Exception:
        return None, None, None

    xnm = info.get("xnm") or {}
    xqm = info.get("xqm") or {}
    return xnm.get("value"), xqm.get("value"), info.get("termText")


async def read_term_any_frame(context) -> tuple[str | None, str | None, str | None]:
    pages = list(context.pages)
    pages.sort(key=lambda p: 0 if any(key in p.url.lower() for key in ("kbcx", "xskbcx")) else 1)
    for pg in pages:
        for frame in pg.frames:
            xnm, xqm, text = await read_term(frame)
            if xnm and xqm:
                return xnm, xqm, text
    return None, None, None


async def read_global_courses(frame) -> list[dict] | None:
    """部分正方页面会把课表直接塞进全局变量。"""
    try:
        data = await frame.evaluate(
            """() => {
                for (const key of ['kbList', 'scheduleList', 'courseList', 'kbListJson']) {
                    if (window[key] && Array.isArray(window[key]) && window[key].length) return window[key];
                }
                for (const s of document.querySelectorAll('script')) {
                    const text = s.textContent || '';
                    const m = text.match(/var\\s+(?:kbList|scheduleList)\\s*=\\s*(\\[[\\s\\S]*?\\]);/);
                    if (m) {
                        try { return JSON.parse(m[1]); } catch (e) {}
                    }
                }
                return null;
            }"""
        )
        return data or None
    except Exception:
        return None


async def fetch_schedule_in_frame(frame, xnm: str, xqm: str) -> list[dict] | None:
    """在课表页面自己的上下文里请求接口，自动携带 Cookie 和 WebVPN 会话。"""
    try:
        data = await frame.evaluate(
            """async ({endpoints, xnm, xqm}) => {
                for (const endpoint of endpoints) {
                    try {
                        const res = await fetch(endpoint, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                                'X-Requested-With': 'XMLHttpRequest'
                            },
                            body: `xnm=${encodeURIComponent(xnm)}&xqm=${encodeURIComponent(xqm)}&kzlx=ck&xsdm=`,
                            credentials: 'include'
                        });
                        const text = await res.text();
                        if (!text || text.trim().startsWith('<')) continue;
                        let json;
                        try { json = JSON.parse(text); } catch (e) { continue; }
                        const list = json.kbList || json.sjkList || (Array.isArray(json) ? json : null);
                        if (Array.isArray(list) && list.length) return list;
                    } catch (e) {}
                }
                return null;
            }""",
            {"endpoints": SCHEDULE_ENDPOINTS, "xnm": xnm, "xqm": xqm},
        )
        return data or None
    except Exception:
        return None


async def collect_courses(context, captured: list[list[dict]]) -> tuple[list[dict] | None, str | None]:
    pages = list(context.pages)
    pages.sort(key=lambda p: 0 if any(key in p.url.lower() for key in ("kbcx", "xskbcx")) else 1)

    # 1. 页面内嵌变量
    for pg in pages:
        for frame in pg.frames:
            items = await read_global_courses(frame)
            if items:
                return items, "页面内嵌数据"

    # 2. 页面自己发出的课表请求
    if captured:
        return captured[-1], "网络请求"

    return None, None


async def describe_pages(context) -> None:
    """抓取失败时列出脚本当前能看到的页面，方便判断是不是开在了新标签页。"""
    log("脚本当前看到的页面：")
    for pg in context.pages:
        try:
            title = await pg.title()
        except Exception:
            title = "?"
        log(f"  - {title} | {pg.url}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="导出河北环境工程学院课表为 WakeUp CSV")
    parser.add_argument("--term", nargs=2, metavar=("学年", "学期"), help="覆盖学期，例如 --term 2025 12")
    parser.add_argument("--no-shorten-room", action="store_true", help="保留完整教室名称")
    args = parser.parse_args()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("缺少 playwright，请先运行 run.bat。")
        return 1

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="msedge", headless=False, args=["--start-maximized"])
        except Exception:
            log("未能启动系统 Edge，尝试使用 Playwright 自带 Chromium……")
            try:
                browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
            except Exception as exc:
                log(f"启动浏览器失败：{exc}")
                log("如果提示缺少浏览器，请运行：python -m playwright install chromium")
                return 1

        context_kwargs = {"locale": "zh-CN", "viewport": {"width": 1366, "height": 900}}
        if STATE_FILE.exists():
            context_kwargs["storage_state"] = str(STATE_FILE)
        try:
            context = await browser.new_context(**context_kwargs)
        except Exception:
            context_kwargs.pop("storage_state", None)
            context = await browser.new_context(**context_kwargs)

        page = await context.new_page()

        closed = asyncio.Event()
        browser.on("disconnected", lambda: closed.set())

        captured: list[list[dict]] = []

        async def handle_response(resp):
            try:
                if resp.request.resource_type not in ("xhr", "fetch"):
                    return
                url = resp.url.lower()
                ctype = (resp.headers.get("content-type") or "").lower()
                # 正方在部分环境下会把 JSON 标成 text/html，所以按 URL 也放行
                if "json" not in ctype and not any(key in url for key in ("kbcx", "xskbcx")):
                    return
                text = await resp.text()
                if not text or ("kcmc" not in text and "kbList" not in text):
                    return
                data = json.loads(text)
                found = extract_course_list(data)
                if found:
                    captured.append(found)
            except Exception:
                pass

        def attach_page(pg):
            pg.on("response", lambda resp: asyncio.create_task(handle_response(resp)))

        attach_page(page)
        # 教务系统可能从 WebVPN 门户里新开标签页，新页面也要一起监听
        context.on("page", attach_page)

        log("正在打开学校 WebVPN……")
        await page.goto(WEBVPN_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)

        log("")
        log("请在浏览器里完成这些操作：")
        log("  1. 登录统一身份认证。")
        log("  2. 进入教务系统。")
        log("  3. 打开「个人课表查询」，选好要导出的学期，等课程显示出来。")
        log("")
        log("准备好后回到这个窗口按回车，脚本会抓取当前页面的课表。")

        items = None
        source = None
        xnm = xqm = None
        term_text = None

        for attempt in range(1, 4):
            await asyncio.to_thread(input, f"\n按回车抓取（第 {attempt}/3 次）...")
            await asyncio.sleep(1.2)

            current_xnm, current_xqm, current_text = await read_term_any_frame(context)
            if args.term:
                xnm, xqm = args.term[0], args.term[1]
            else:
                xnm, xqm = current_xnm, current_xqm
            if current_text:
                term_text = current_text

            items, source = await collect_courses(context, captured)

            if not items:
                if not xnm or not xqm:
                    xnm, xqm = guess_term()
                for pg in context.pages:
                    for frame in pg.frames:
                        if any(key in frame.url.lower() for key in ("kbcx", "xskbcx")):
                            items = await fetch_schedule_in_frame(frame, xnm, xqm)
                            if items:
                                source = "课表接口"
                                break
                    if items:
                        break

            if items:
                break

            log("当前页面没有检测到课表数据。")
            log("请确认已经进入教务系统的「个人课表查询」页面，课程已经显示出来，并且选好了学期。")

        if not items:
            log("")
            await describe_pages(context)
            log("")
            log("还是没拿到课程数据。可以把当前页面截图，或 F12 → Network 里返回课表的那条请求发给我。")
            log("浏览器保留在这个页面，关闭后脚本退出。")
            try:
                await asyncio.wait_for(closed.wait(), timeout=1800)
            except asyncio.TimeoutError:
                pass
            return 3

        if not xnm or not xqm:
            xnm, xqm = guess_term()

        courses = courses_from_items(items, shorten_room=not args.no_shorten_room)
        if not courses:
            log("拿到了数据，但没有解析出有效课程。")
            return 3

        if term_text:
            term = term_text
        else:
            term = term_label(xnm, xqm)

        output = write_csv(courses, term)

        log("")
        log(f"获取成功：{len(courses)} 条课程（来源：{source}，学期：{term}）")
        log(f"已生成：{output}")
        log("把 CSV 发到手机，用 WakeUp 课程表的「从 Excel 导入」打开即可。")
        log("")
        log("浏览器不会自动关闭。关掉浏览器后脚本自动退出。")

        try:
            if sys.platform == "win32":
                os.startfile(str(OUTPUT_DIR))  # noqa: S606
        except Exception:
            pass

        try:
            await asyncio.wait_for(closed.wait(), timeout=1800)
        except asyncio.TimeoutError:
            log("等待超时，脚本退出。")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
