"""本地预览 QQ 消息样式，不发群、不读文档、不需要网络。

改了 send_qq.py 的 emoji 表 / 折叠规则 / 标题格式后，先跑这个看效果：

    python menu/scripts/preview_style.py

样例数据取自学校真实食谱图（已在 menu/inbox/processed/ 归档），
覆盖了三种典型形态：面食主打日、米饭日、无主菜的晚餐。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
from send_qq import build_message  # noqa: E402

DAYS = {
    "2026-10-26": {"date": "2026-10-26", "weekday": "周一", "meals": {
        "早餐": ["花卷", "馒头", "红薯", "白菜粉条", "蒸鸡蛋", "胡辣汤"],
        "午餐": ["大盘鸡拌面", "白灼青菜", "鸡蛋面汤", "馒头"],
        "晚餐": ["木须肉片", "干锅花菜", "大米粥", "馒头"]}},
    "2026-10-27": {"date": "2026-10-27", "weekday": "周二", "meals": {
        "早餐": ["蒸肉卷", "玉米", "酸辣土豆丝", "蒸鸡蛋", "奶香豆浆"],
        "午餐": ["清炖排骨", "家常豆腐", "耗油生菜", "紫菜蛋花汤", "米饭"],
        "晚餐": ["麻汁涮菜", "热合鸡蛋", "红豆粥", "玉米发糕"]}},
    "2026-10-29": {"date": "2026-10-29", "weekday": "周四", "meals": {
        "早餐": ["南瓜卷", "馒头", "玉米", "黄豆芽炒粉条", "蒸鸡蛋", "胡辣汤"],
        "午餐": ["红烧肉", "西红柿炒鸡蛋", "蒜香青菜", "鸡蛋醪糟汤", "米饭"],
        "晚餐": ["奥尔良鸡排", "扬州炒饭", "海米冬瓜", "八宝粥", "馒头"]}},
    "2026-10-23": {"date": "2026-10-23", "weekday": "周五", "meals": {
        "早餐": ["花卷", "馒头", "红薯", "手撕包菜", "蒸鸡蛋", "黑米粥", "牛奶"],
        "午餐": ["酸汤水饺", "玉米面汤", "馒头"]}},
    "2026-10-25": {"date": "2026-10-25", "weekday": "周日", "meals": {
        "晚餐": ["肉片炒面筋", "芹菜炒花生", "红豆粥", "馒头"]}},
}

# (标题, 当天, 次日, 返校晚餐)
CASES = [
    ("面食主打 + 次日早餐", "2026-10-26", "2026-10-27", None),
    ("米饭日 + 无主菜的晚餐", "2026-10-27", None, None),
    ("米饭日 + 炒饭配菜", "2026-10-29", None, None),
    ("周五离校 + 周日晚餐预告", "2026-10-23", None, "2026-10-25"),
]

for title, today, nxt, resume in CASES:
    msg = build_message(
        DAYS[today],
        DAYS.get(nxt) if nxt else None,
        DAYS.get(resume) if resume else None,
    )
    print("=" * 46)
    print(f"【{title}】 长度 {len(msg)} 字")
    print("=" * 46)
    print(msg)
    print()

quiet = build_message({"date": "2026-10-31", "meals": {}})
print("=" * 46)
print(f"【安静日】返回 {quiet!r}（空字符串 = 什么都不发）")
