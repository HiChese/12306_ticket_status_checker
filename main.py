"""
12306 车票查询 — 单文件入口
  无参数        → 启动 C++ 交互界面
  query <参数>  → Python 查询引擎（车站解析 + 12306 请求）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile


# ==================================================================
# 查询引擎
# ==================================================================

def cmd_query(args: list[str]) -> None:
    """查询模式：main.py query <date> <from> <to> -o <file>"""
    import argparse
    from query_12306 import query_tickets

    parser = argparse.ArgumentParser()
    parser.add_argument("date")
    parser.add_argument("from_city")
    parser.add_argument("to_city")
    parser.add_argument("-o", "--output", required=True)
    opts = parser.parse_args(args)

    result = query_tickets(opts.date, opts.from_city, opts.to_city)
    out_dir = os.path.dirname(os.path.abspath(opts.output)) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(opts.output, "w", encoding="utf-8") as f:
        json.dump(result["_raw_response"], f, ensure_ascii=False, indent=2)
    print(f"OK:{os.path.abspath(opts.output)}")


# ==================================================================
# UI 启动器
# ==================================================================

def cmd_ui() -> None:
    """交互模式：提取内嵌的 C++ display.exe 并启动"""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    display_exe = os.path.join(base, "display.exe")
    if not os.path.exists(display_exe):
        print("[错误] 未找到 display.exe")
        input("按回车键退出...")
        return

    tmp_dir = tempfile.gettempdir()
    tmp_display = os.path.join(tmp_dir, "12306_display.exe")
    tmp_engine = os.path.join(tmp_dir, "query_engine.exe")

    shutil.copy2(display_exe, tmp_display)

    # 将自身复制为 query_engine.exe，供 C++ 调用
    if getattr(sys, "frozen", False):
        shutil.copy2(sys.executable, tmp_engine)

    # 启动 C++ UI
    os.chdir(tmp_dir)
    subprocess.run([tmp_display])

    # 清理
    try:
        os.remove(tmp_display)
    except Exception:
        pass


# ==================================================================
# 入口
# ==================================================================

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "query":
        cmd_query(sys.argv[2:])
    else:
        cmd_ui()
