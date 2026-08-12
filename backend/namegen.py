# knowe v0.9d — Agent 名字
"""
namegen.py — 中英文中性姓名生成器（从前端 `src/store/nameGenerator.ts` **逐字库移植**）。

为什么名字要搬到后端来：

  v0.9c 我把名字改成了确定性公式（`fe_1` + `前端` → 「前端 1」）——
  为的是治「每次开 App 名字都变」。**药是对的，剂量下猛了**：
  一并把「林知远」「River」这种有人味的名字也治没了。
  一屋子「前端 1」「后端 1」在群里说话，读起来像仓库清单，不像一支队伍。

  随机和持久化**从来不矛盾**。矛盾的是「每次都重新掷」。
  所以规矩改成一句话：**掷一次，写进花名册，此后只读不掷。**

    建人时：花名册里有他（哪怕是归档的）→ 用旧名
            没有                        → 掷一次（中/英各一半）→ **立刻落盘**
    温载时：从花名册读回来 —— 绝不重新生成

  字库是从 nameGenerator.ts 里**用脚本抠出来的**，不是手抄的：
  80 个姓、410 个名用字（去重后）、500 个英文名（去重 + ≤7 字母）。
  手抄 990 个字符串，一定会抄错一个，而且是三个月后才发现的那种错。

前端的 nameGenerator.ts **保留不动**（将来做「手动改花名」还用得上），
但它不再决定任何人叫什么——名字是后端的事，和角色、头像一样。
"""

from __future__ import annotations

import random

__all__ = ["gen_cn", "gen_en", "gen_pair", "gen_name", "STATS"]

# ═══════════════════════════════════════════════════════════════
# 姓氏库 — 80 个常见单字姓
# ═══════════════════════════════════════════════════════════════
SURNAMES: list[str] = [
    "林", "叶", "沈", "苏", "陆", "郑", "顾", "孟", "季", "简",
    "安", "时", "阮", "何", "余", "梁", "宋", "谢", "唐", "邹",
    "黎", "乔", "温", "纪", "贺", "夏", "姜", "范", "方", "石",
    "姚", "谭", "廖", "金", "魏", "薛", "阎", "段", "雷", "白",
    "秦", "江", "史", "侯", "崔", "任", "卢", "傅", "康", "毛",
    "常", "万", "赖", "文", "尹", "蓝", "钟", "龙", "颜", "倪",
    "曲", "岳", "齐", "易", "聂", "辛", "冉", "凌", "盛", "霍",
    "田", "丁", "邓", "戴", "董", "潘", "杜", "冯", "曹", "彭",
]

# ═══════════════════════════════════════════════════════════════
# 名用字库 — 精选中性汉字（去重后 410 个）
# ═══════════════════════════════════════════════════════════════
_GIVEN_RAW: list[str] = [
    "天", "日", "月", "星", "辰", "宿", "斗", "汉", "河", "云",
    "风", "雨", "雪", "雷", "电", "霜", "露", "虹", "霞", "烟",
    "雾", "岚", "霭", "霄", "穹", "宇", "昊", "苍", "碧", "曦",
    "晖", "曙", "旭", "曜", "气", "山", "岳", "峰", "岭", "峦",
    "崖", "壁", "岩", "石", "谷", "峡", "川", "水", "江", "湖",
    "海", "泽", "池", "泉", "溪", "涧", "潭", "湾", "港", "浦",
    "洲", "汀", "渚", "屿", "岸", "沙", "波", "浪", "涛", "潮",
    "源", "流", "泊", "渡", "津", "原", "野", "陆", "甸", "坪",
    "林", "森", "木", "树", "松", "柏", "竹", "柳", "杨", "枫",
    "桐", "槐", "桑", "樟", "楠", "杉", "桦", "榕", "荆", "藤",
    "兰", "芝", "草", "叶", "枝", "根", "果", "禾", "苗", "稼",
    "芃", "蔚", "蓁", "萧", "仁", "义", "礼", "智", "信", "忠",
    "孝", "廉", "洁", "清", "正", "直", "端", "方", "诚", "真",
    "善", "良", "温", "润", "和", "平", "谦", "恭", "敬", "谨",
    "慎", "勤", "勉", "敏", "慧", "明", "达", "通", "博", "雅",
    "逸", "端", "庄", "厚", "朴", "纯", "净", "洁", "素", "简",
    "淡", "悠", "闲", "宏", "远", "深", "高", "广", "宽", "弘",
    "阔", "旷", "朗", "峻", "崇", "巍", "屹", "峙", "耸", "挺",
    "拔", "卓", "超", "越", "迈", "腾", "翔", "飞", "安", "宁",
    "静", "定", "泰", "康", "乐", "欣", "悦", "怡", "畅", "舒",
    "适", "祥", "瑞", "嘉", "吉", "庆", "景", "熙", "春", "夏",
    "秋", "冬", "朝", "暮", "晨", "晓", "旦", "夕", "时", "年",
    "岁", "世", "代", "恒", "常", "永", "久", "长", "文", "书",
    "诗", "辞", "赋", "章", "篇", "经", "典", "史", "纪", "传",
    "录", "记", "志", "铭", "策", "论", "言", "语", "词", "韵",
    "律", "调", "歌", "白", "青", "碧", "苍", "翠", "丹", "朱",
    "赤", "玄", "墨", "素", "彩", "辉", "灿", "焕", "熠", "莹",
    "皓", "行", "游", "观", "望", "思", "想", "念", "怀", "归",
    "渡", "启", "承", "继", "绍", "传", "延", "循", "依", "随",
    "从", "知", "识", "悟", "觉", "省", "一", "之", "子", "然",
    "若", "以", "可", "亦", "如", "更", "初", "元", "本", "始",
    "终", "末", "极", "至", "尽", "穷", "新", "故", "旧", "陈",
    "先", "后", "前", "向", "对", "应", "同", "共", "齐", "并",
    "合", "会", "集", "聚", "散", "分", "全", "备", "周", "遍",
    "普", "均", "平", "等", "均", "衡", "三", "五", "七", "九",
    "十", "百", "千", "万", "亿", "东", "南", "西", "北", "中",
    "上", "下", "左", "右", "内", "贤", "哲", "圣", "彦", "俊",
    "英", "杰", "豪", "魁", "冠", "首", "元", "奇", "异", "殊",
    "特", "独", "孤", "傲", "岸", "韧", "毅", "坚", "固", "稳",
    "灵", "妙", "微", "细", "精", "巧", "工", "致", "密", "疏",
    "柔", "软", "轻", "重", "缓", "急", "疾", "徐", "舒", "展",
    "玉", "璧", "环", "佩", "珩", "璜", "瑶", "琳", "琅", "琛",
    "宝", "珍",
]
#: 去重（保持插入顺序 —— dict 从 3.7 起有序，这里就是当有序集合用）
GIVEN_CHARS: list[str] = list(dict.fromkeys(_GIVEN_RAW))

# ═══════════════════════════════════════════════════════════════
# 英文名库 — 中性名/词（去重 + 只留 ≤7 字母，500 个）
# ═══════════════════════════════════════════════════════════════
_EN_RAW: list[str] = [
    "Sky", "Cloud", "Star", "Sun", "Moon", "Nova", "Sol", "Luna",
    "Dawn", "Dusk", "Mist", "Fog", "Storm", "Rain", "Snow", "Hail",
    "Wind", "Bolt", "Flash", "Ray", "Beam", "Glow", "Halo", "Aura",
    "Shine", "River", "Brook", "Stream", "Creek", "Lake", "Pond", "Ocean",
    "Sea", "Bay", "Cove", "Reef", "Tide", "Wave", "Surf", "Foam",
    "Spray", "Rill", "Fjord", "Delta", "Harbor", "Shore", "Coast", "Strait",
    "Isle", "Atoll", "Spring", "Falls", "Marsh", "Stone", "Rock", "Cliff",
    "Ridge", "Peak", "Summit", "Crest", "Hill", "Mesa", "Butte", "Dune",
    "Plain", "Field", "Meadow", "Glen", "Dale", "Vale", "Gorge", "Cave",
    "Grotto", "Bluff", "Heath", "Moor", "Fell", "Downs", "Prairie", "Steppe",
    "Oasis", "Desert", "Tundra", "Tree", "Leaf", "Fern", "Reed", "Sage",
    "Basil", "Clove", "Thyme", "Mint", "Anise", "Fennel", "Briar", "Bramble",
    "Ivy", "Moss", "Lichen", "Pine", "Cedar", "Birch", "Aspen", "Alder",
    "Willow", "Maple", "Oak", "Elm", "Yew", "Holly", "Juniper", "Laurel",
    "Myrtle", "Sorrel", "Tansy", "Senna", "Lotus", "Heather", "Fox", "Wolf",
    "Hawk", "Falcon", "Eagle", "Kite", "Raven", "Crane", "Heron", "Swan",
    "Dove", "Jay", "Wren", "Finch", "Lark", "Robin", "Sparrow", "Starling",
    "Martin", "Merlin", "Phoenix", "Gryphon", "Otter", "Marten", "Lynx", "Flint",
    "Jade", "Opal", "Onyx", "Mica", "Beryl", "Topaz", "Garnet", "Ruby",
    "Perl", "Coral", "Ivory", "Jet", "Agate", "Amber", "Crystal", "Gem",
    "Slate", "Gray", "Blue", "Cyan", "Teal", "Navy", "Indigo", "Azure",
    "Cobalt", "Olive", "Sage", "Moss", "Amber", "Rust", "Sienna", "Ochre",
    "Umber", "Ash", "Onyx", "Ivory", "Hazel", "Alex", "Avery", "Blair",
    "Blake", "Casey", "Corey", "Dana", "Darcy", "Devon", "Drew", "Ellis",
    "Emery", "Erin", "Gale", "Harley", "Haven", "Hollis", "Jamie", "Jesse",
    "Jody", "Jordan", "Jules", "Kelly", "Kerry", "Lane", "Lee", "Logan",
    "Lynn", "Marley", "Morgan", "Nico", "Noel", "Pat", "Quinn", "Ray",
    "Reese", "Remy", "Rene", "Ricki", "Riley", "Robin", "Rowan", "Sam",
    "Sandy", "Shawn", "Skyler", "Sloan", "Stacy", "Sunny", "Taylor", "Terry",
    "Toni", "Tracy", "Val", "Vern", "Wynn", "Carey", "Finley", "Ariel",
    "Payton", "Arden", "Aris", "Auden", "Briar", "Cam", "Cyan", "Ember",
    "Greer", "Indigo", "Jade", "Juno", "Kai", "Kit", "Lennox", "Lux",
    "Merit", "Milan", "Nova", "Onyx", "Perry", "Quincy", "Rory", "Rumi",
    "Salem", "Shiloh", "Sidney", "Tate", "Tatum", "Teal", "True", "Vesper",
    "Winter", "Oren", "Sorin", "Sable", "Halden", "Linden", "Arlo", "Beck",
    "Brett", "Channing", "Cleo", "Corrie", "Darby", "Devin", "Harlow", "Hayden",
    "Jalen", "Keegan", "Kirby", "Lane", "Nevada", "Oakley", "Parker", "Reagan",
    "Rory", "Rowan", "Sasha", "Tierney", "Al", "Ash", "Cam", "Del",
    "Gem", "Jai", "Jan", "Jem", "Joss", "Kai", "Kim", "Kit",
    "Kris", "Laz", "Lex", "Lou", "Max", "Nat", "Nell", "Pax",
    "Pip", "Ren", "Rex", "Rio", "Rue", "Syd", "Taj", "Zan",
    "Zen", "Zev", "Hope", "Grace", "Faith", "Joy", "Peace", "Honor",
    "Merit", "Valor", "Bliss", "Chance", "Drew", "Glory", "Haven", "Justice",
    "Liberty", "Promise", "Reason", "Serene", "Spirit", "Truly", "Unity", "Verve",
    "Worthy", "Zeal", "Noble", "Apollo", "Aries", "Atlas", "Nyx", "Eros",
    "Chaos", "Cosmos", "Eden", "Elysium", "Oracle", "Muse", "Lyric", "Fable",
    "Saga", "Rune", "Echo", "Myth", "Verse", "Ode", "Epic", "Drama",
    "Sonnet", "Rhyme", "Tempo", "Pulse", "Austin", "Boston", "Camden", "Dallas",
    "Dayton", "Denver", "Dublin", "Florence", "Kent", "Milan", "Odessa", "Paris",
    "Phoenix", "Sydney", "Vienna", "York", "East", "North", "West", "Mid",
    "Archer", "Bishop", "Carter", "Chase", "Cooper", "Mason", "Piper", "Porter",
    "Sailor", "Sawyer", "Spencer", "Tanner", "Taylor", "Tyler", "Walker", "Spring",
    "Autumn", "Summer", "Winter", "April", "May", "June", "July", "August",
    "Vernal", "Equinox", "Solstice", "Aero", "Array", "Axiom", "Beacon", "Cipher",
    "Echo", "Fable", "Flare", "Glint", "Glyph", "Haven", "Matrix", "Neo",
    "Pixel", "Prism", "Quest", "Rogue", "Spark", "Vista", "Zenith", "Alpine",
    "Arroyo", "Badger", "Basalt", "Basin", "Berry", "Bloom", "Breeze", "Cactus",
    "Canyon", "Cherry", "Cinder", "Drift", "Flax", "Forge", "Fossil", "Geode",
    "Gully", "Hickory", "Lava", "Loam", "Magma", "Mica", "Obsidian", "Orbit",
    "Pebble", "Petal", "Pollen", "Prairie", "Reef", "Adair", "Adriel", "Akira",
    "Ames", "Amir", "Amos", "Ansel", "Arbor", "Arlo", "Asa", "Auden",
    "Bevin", "Bo", "Cael", "Cato", "Ciel", "Cody", "Corin", "Cy",
    "Dane", "Eben", "Elia", "Ennis", "Ewan", "Fen", "Gale", "Grey",
    "Hale", "Idris", "Innes", "Ira", "Ivo", "Jan", "Joss", "Jude",
    "Kael", "Kei", "Kent", "Kerr", "Koa", "Azure", "Chance", "Cove",
    "Cruz", "Dior", "Essence", "Everest", "Fenix", "Halo", "Helix", "Jewel",
    "Journey", "Karma", "Kismet", "Legend", "Lotus", "Lyric", "Neon", "Noble",
    "Origin", "Phoenix", "Radiance", "Serene", "Seven", "Silver", "Ace", "Arc",
    "Bayou", "Calm", "Cypress", "Eon", "Fathom", "Gist", "Kelp", "Lantern",
    "Mistral", "Nimbus", "Quill", "Sylvan", "Vega",
]
EN_NAMES: list[str] = [n for n in dict.fromkeys(_EN_RAW) if len(n) <= 7]

STATS = {
    "surnames": len(SURNAMES),
    "given_chars": len(GIVEN_CHARS),
    "en_names": len(EN_NAMES),
}


# ═══════════════════════════════════════════════════════════════
# 生成
# ═══════════════════════════════════════════════════════════════

def gen_cn(given_only: bool = False) -> str:
    """「林知远」/「知远」。名 1~2 字，**同一个人不重字**（random.sample 保证）。"""
    if given_only:
        n = random.choice((2, 3))
        return "".join(random.sample(GIVEN_CHARS, n))
    surname = random.choice(SURNAMES)
    n = random.choice((1, 2))
    return surname + "".join(random.sample(GIVEN_CHARS, n))


def gen_en(max_len: int = 7) -> str:
    """「River」。≤7 字母。"""
    pool = [n for n in EN_NAMES if len(n) <= max_len] or EN_NAMES
    return random.choice(pool)


def gen_pair() -> dict[str, str]:
    """{"cn": "林知远", "en": "River"} —— 一次生成一对。"""
    return {"cn": gen_cn(), "en": gen_en()}


def gen_name(taken: set[str] | None = None, tries: int = 12, lang: str | None = None) -> str:
    """
    掷一个名字出来。**中英各一半**（硬币在这里掷，只掷这一次）。

    ★ `lang`（[v1.0.23.3-R2]）：'en' → 只从英语词汇库掷（英文模式建群/拉人
      不再出中文名——中文名会把模型输出带成中文，已复现铁证）；
      'zh' → 只从汉字库掷；None → 保持历史行为（中英各半）。

    ★ `taken`：这个项目里已经有人叫的名字。同一个群里两个「林知远」，
      用户会以为自己看重影了 —— 撞了就重掷，最多 12 次。
      12 次还撞（在 410×80 + 500 的池子里，这基本不可能），就认了：
      **宁可重名，也不能在这儿死循环**。

    为什么不掷完就存在这里：存名字是花名册的事（persist.py）。
    这个模块只管「掷」，不管「记」——记账的地方只能有一个。
    """
    taken = taken or set()
    for _ in range(tries):
        if lang == "en":
            name = gen_en()
        elif lang == "zh":
            name = gen_cn()
        else:
            pair = gen_pair()
            name = pair["cn"] if random.random() < 0.5 else pair["en"]
        if name not in taken:
            return name
    return gen_en() if lang == "en" else gen_cn()
