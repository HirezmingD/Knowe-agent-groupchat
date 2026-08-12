/**
 * nameGenerator.ts — 中英文中性姓名生成器（R3-2）
 *
 * 从 name_generator.py 逐字库移植。零依赖，只用 Math.random。
 *
 * API:
 *   genCn()         → "林知远"（姓+名，2~3字全名）
 *   genCn(true)     → "知远"（仅名不含姓）
 *   genEn()         → "River"（≤7字母）
 *   genEn(7, 'nature')   → 只要自然词风格
 *   genPair()       → { cn: "林知远", en: "River" }
 *   genBatch(10)    → [{cn, en}, ...]
 */

// ═══════════════════════════════════════════════════════════════
// 姓氏库 — ~80个常见单字姓
// ═══════════════════════════════════════════════════════════════
const SURNAMES = [
  '林','叶','沈','苏','陆','郑','顾','孟','季','简',
  '安','时','阮','何','余','梁','宋','谢','唐','邹',
  '黎','乔','温','纪','贺','夏','姜','范','方','石',
  '姚','谭','廖','金','魏','薛','阎','段','雷','白',
  '秦','江','史','侯','崔','任','卢','傅','康','毛',
  '常','万','赖','文','尹','蓝','钟','龙','颜','倪',
  '曲','岳','齐','易','聂','辛','冉','凌','盛','霍',
  '田','丁','邓','戴','董','潘','杜','冯','曹','彭',
];

// ═══════════════════════════════════════════════════════════════
// 名用字库 — ~430 个精选中性汉字
// ═══════════════════════════════════════════════════════════════
const GIVEN_CHARS = [
  // ── 天文气象（35字）──
  '天','日','月','星','辰','宿','斗','汉','河',
  '云','风','雨','雪','雷','电','霜','露','虹','霞',
  '烟','雾','岚','霭','霄','穹','宇','昊','苍','碧',
  '曦','晖','曙','旭','曜','气',
  // ── 山水地理（45字）──
  '山','岳','峰','岭','峦','崖','壁','岩','石','谷',
  '峡','川','水','江','湖','海','泽','池','泉','溪',
  '涧','潭','湾','港','浦','洲','汀','渚','屿','岸',
  '沙','波','浪','涛','潮','源','流','泊','渡','津',
  '原','野','陆','甸','坪',
  // ── 草木植物（35字）──
  '林','森','木','树','松','柏','竹','柳','杨','枫',
  '桐','槐','桑','樟','楠','杉','桦','榕','荆','藤',
  '兰','芝','草','叶','枝','根','果','禾','苗','稼',
  '芃','蔚','蓁','萧',
  // ── 品质德性（50字）──
  '仁','义','礼','智','信','忠','孝','廉','洁','清',
  '正','直','端','方','诚','真','善','良','温','润',
  '和','平','谦','恭','敬','谨','慎','勤','勉','敏',
  '慧','明','达','通','博','雅','逸','端','庄','厚',
  '朴','纯','净','洁','素','简','淡','悠','闲',
  // ── 气度格局（25字）──
  '宏','远','深','高','广','宽','弘','阔','旷','朗',
  '峻','崇','巍','屹','峙','耸','挺','拔','卓','超',
  '越','迈','腾','翔','飞',
  // ── 安宁康乐（20字）──
  '安','宁','静','定','泰','康','乐','欣','悦','怡',
  '畅','舒','适','祥','瑞','嘉','吉','庆','景','熙',
  // ── 时空节律（20字）──
  '春','夏','秋','冬','朝','暮','晨','晓','旦','夕',
  '时','年','岁','世','代','恒','常','永','久','长',
  // ── 文化学识（25字）──
  '文','书','诗','辞','赋','章','篇','经','典','史',
  '纪','传','录','记','志','铭','策','论','言','语',
  '词','韵','律','调','歌',
  // ── 光彩色泽（18字）──
  '白','青','碧','苍','翠','丹','朱','赤','玄','墨',
  '素','彩','辉','灿','焕','熠','莹','皓',
  // ── 动作神态（25字）──
  '行','游','观','望','思','想','念','怀','归','渡',
  '启','承','继','绍','传','延','循','依','随','从',
  '知','识','悟','觉','省',
  // ── 抽象意境（50字）──
  '一','之','子','然','若','以','可','亦','如','更',
  '初','元','本','始','终','末','极','至','尽','穷',
  '新','故','旧','陈','先','后','前','向','对','应',
  '同','共','齐','并','合','会','集','聚','散','分',
  '全','备','周','遍','普','均','平','等','均','衡',
  // ── 数量方位（20字）──
  '三','五','七','九','十','百','千','万','亿',
  '东','南','西','北','中','上','下','左','右','内',
  // ── 品格补充（25字）──
  '贤','哲','圣','彦','俊','英','杰','豪','魁','冠',
  '首','元','奇','异','殊','特','独','孤','傲','岸',
  '韧','毅','坚','固','稳',
  // ── 灵妙音形（20字）──
  '灵','妙','微','细','精','巧','工','致','密','疏',
  '柔','软','轻','重','缓','急','疾','徐','舒','展',
  // ── 玉器珍宝（12字）──
  '玉','璧','环','佩','珩','璜','瑶','琳','琅','琛',
  '宝','珍',
];

// 去重（保持插入顺序）
function dedupe<T>(arr: T[]): T[] { return [...new Set(arr)]; }
const GIVEN_CHARS_DEDUPED = dedupe(GIVEN_CHARS);

// ═══════════════════════════════════════════════════════════════
// 英文名词库 — ~520 个中性名/词，≤7 字母
// ═══════════════════════════════════════════════════════════════
const EN_NAMES_RAW = [
  // ── 自然-天空（25）──
  'Sky','Cloud','Star','Sun','Moon','Nova','Sol','Luna',
  'Dawn','Dusk','Mist','Fog','Storm','Rain','Snow','Hail',
  'Wind','Bolt','Flash','Ray','Beam','Glow','Halo','Aura','Shine',
  // ── 自然-水（28）──
  'River','Brook','Stream','Creek','Lake','Pond','Ocean',
  'Sea','Bay','Cove','Reef','Tide','Wave','Surf','Foam','Spray',
  'Rill','Fjord','Delta','Harbor','Shore','Coast','Strait','Isle',
  'Atoll','Spring','Falls','Marsh',
  // ── 自然-陆地（30）──
  'Stone','Rock','Cliff','Ridge','Peak','Summit','Crest','Hill',
  'Mesa','Butte','Dune','Plain','Field','Meadow','Glen','Dale',
  'Vale','Gorge','Cave','Grotto','Bluff','Heath','Moor','Fell',
  'Downs','Prairie','Steppe','Oasis','Desert','Tundra',
  // ── 自然-植物（35）──
  'Tree','Leaf','Fern','Reed','Sage','Basil','Clove','Thyme',
  'Mint','Anise','Fennel','Briar','Bramble','Ivy','Moss','Lichen',
  'Pine','Cedar','Birch','Aspen','Alder','Willow','Maple','Oak',
  'Elm','Yew','Holly','Juniper','Laurel','Myrtle','Sorrel','Tansy',
  'Senna','Lotus','Heather',
  // ── 自然-动物（25）──
  'Fox','Wolf','Hawk','Falcon','Eagle','Kite','Raven','Crane',
  'Heron','Swan','Dove','Jay','Wren','Finch','Lark','Robin',
  'Sparrow','Starling','Martin','Merlin','Phoenix','Gryphon',
  'Otter','Marten','Lynx',
  // ── 矿物宝石（18）──
  'Flint','Jade','Opal','Onyx','Mica','Beryl','Topaz','Garnet',
  'Ruby','Perl','Coral','Ivory','Jet','Agate','Amber','Crystal',
  'Gem','Slate',
  // ── 颜色（20）──
  'Gray','Blue','Cyan','Teal','Navy','Indigo','Azure','Cobalt',
  'Olive','Sage','Moss','Amber','Rust','Sienna','Ochre','Umber',
  'Ash','Onyx','Ivory','Hazel',
  // ── 传统中性名（60）──
  'Alex','Avery','Blair','Blake','Casey','Corey','Dana','Darcy',
  'Devon','Drew','Ellis','Emery','Erin','Gale','Harley','Haven',
  'Hollis','Jamie','Jesse','Jody','Jordan','Jules','Kelly','Kerry',
  'Lane','Lee','Logan','Lynn','Marley','Morgan','Nico','Noel',
  'Pat','Quinn','Ray','Reese','Remy','Rene','Ricki','Riley',
  'Robin','Rowan','Sam','Sandy','Shawn','Skyler','Sloan','Stacy',
  'Sunny','Taylor','Terry','Toni','Tracy','Val','Vern','Wynn',
  'Carey','Finley','Ariel','Payton',
  // ── 现代中性名（50）──
  'Arden','Aris','Auden','Briar','Cam','Cyan','Ember','Greer',
  'Indigo','Jade','Juno','Kai','Kit','Lennox','Lux','Merit',
  'Milan','Nova','Onyx','Perry','Quincy','Rory','Rumi','Salem',
  'Shiloh','Sidney','Tate','Tatum','Teal','True','Vesper',
  'Winter','Oren','Sorin','Sable','Halden','Linden','Arlo',
  'Beck','Brett','Channing','Cleo','Corrie','Darby','Devin',
  'Harlow','Hayden','Jalen','Keegan','Kirby','Lane','Nevada',
  'Oakley','Parker','Reagan','Rory','Rowan','Sasha','Tierney',
  // ── 短名/变体（30）──
  'Al','Ash','Cam','Del','Gem','Jai','Jan','Jem','Joss',
  'Kai','Kim','Kit','Kris','Laz','Lex','Lou','Max','Nat',
  'Nell','Pax','Pip','Ren','Rex','Rio','Rue','Syd','Taj',
  'Zan','Zen','Zev',
  // ── 美德/抽象词（25）──
  'Hope','Grace','Faith','Joy','Peace','Honor','Merit','Valor',
  'Bliss','Chance','Drew','Glory','Haven','Justice','Liberty',
  'Promise','Reason','Serene','Spirit','Truly','Unity','Verve',
  'Worthy','Zeal','Noble',
  // ── 文化/神话（25）──
  'Apollo','Aries','Atlas','Nyx','Eros','Chaos','Cosmos',
  'Eden','Elysium','Oracle','Muse','Lyric','Fable','Saga',
  'Rune','Echo','Myth','Verse','Ode','Epic','Drama','Sonnet',
  'Rhyme','Tempo','Pulse',
  // ── 地名/方位来源（20）──
  'Austin','Boston','Camden','Dallas','Dayton','Denver','Dublin',
  'Florence','Kent','Milan','Odessa','Paris','Phoenix','Sydney',
  'Vienna','York','East','North','West','Mid',
  // ── 职业/身份转化名（15）──
  'Archer','Bishop','Carter','Chase','Cooper','Mason','Piper',
  'Porter','Sailor','Sawyer','Spencer','Tanner','Taylor','Tyler',
  'Walker',
  // ── 自然-时间/季节（12）──
  'Spring','Autumn','Summer','Winter','April','May','June',
  'July','August','Vernal','Equinox','Solstice',
  // ── 混合/创意（20）──
  'Aero','Array','Axiom','Beacon','Cipher','Echo','Fable',
  'Flare','Glint','Glyph','Haven','Matrix','Neo','Pixel',
  'Prism','Quest','Rogue','Spark','Vista','Zenith',
  // ── 更多自然词（30）──
  'Alpine','Arroyo','Badger','Basalt','Basin','Berry',
  'Bloom','Breeze','Cactus','Canyon','Cherry','Cinder',
  'Drift','Flax','Forge','Fossil','Geode','Gully',
  'Hickory','Lava','Loam','Magma','Mica','Obsidian',
  'Orbit','Pebble','Petal','Pollen','Prairie','Reef',
  // ── 更多短中性名（35）──
  'Adair','Adriel','Akira','Ames','Amir','Amos',
  'Ansel','Arbor','Arlo','Asa','Auden','Bevin',
  'Bo','Cael','Cato','Ciel','Cody','Corin','Cy',
  'Dane','Eben','Elia','Ennis','Ewan','Fen','Gale',
  'Grey','Hale','Idris','Innes','Ira','Ivo','Jan',
  'Joss','Jude','Kael','Kei','Kent','Kerr','Koa',
  // ── 更多现代创意名（25）──
  'Azure','Chance','Cove','Cruz','Dior','Essence',
  'Everest','Fenix','Halo','Helix','Jewel','Journey',
  'Karma','Kismet','Legend','Lotus','Lyric','Neon',
  'Noble','Origin','Phoenix','Radiance','Serene',
  'Seven','Silver',
  // ── 补充（25）──
  'Ace','Arc','Bayou','Calm','Cypress','Eon',
  'Fathom','Gist','Kelp','Lantern','Mistral',
  'Nimbus','Quill','Sylvan','Vega',
];

// 去重并过滤 ≤7 字母
const EN_NAMES = dedupe(EN_NAMES_RAW).filter((n) => n.length <= 7);

// ═══════════════════════════════════════════════════════════════
// 分类池（用于 mode 参数）
// ═══════════════════════════════════════════════════════════════
const EN_NATURE = new Set([
  'Sky','Cloud','Star','Sun','Moon','Nova','Sol','Luna',
  'Dawn','Dusk','Mist','Fog','Storm','Rain','Snow','Hail',
  'Wind','Bolt','Flash','Ray','Beam','Glow','Halo','Aura','Shine',
  'River','Brook','Stream','Creek','Lake','Pond','Ocean',
  'Sea','Bay','Cove','Reef','Tide','Wave','Surf','Foam','Spray',
  'Rill','Fjord','Delta','Harbor','Shore','Coast','Strait','Isle',
  'Atoll','Spring','Falls','Marsh',
  'Stone','Rock','Cliff','Ridge','Peak','Summit','Crest','Hill',
  'Mesa','Butte','Dune','Plain','Field','Meadow','Glen','Dale',
  'Vale','Gorge','Cave','Grotto','Bluff','Heath','Moor','Fell',
  'Downs','Prairie','Steppe','Oasis','Desert','Tundra',
  'Tree','Leaf','Fern','Reed','Sage','Basil','Clove','Thyme',
  'Mint','Anise','Fennel','Briar','Bramble','Ivy','Moss','Lichen',
  'Pine','Cedar','Birch','Aspen','Alder','Willow','Maple','Oak',
  'Elm','Yew','Holly','Juniper','Laurel','Myrtle','Sorrel','Tansy',
  'Senna','Lotus','Heather',
  'Fox','Wolf','Hawk','Falcon','Eagle','Kite','Raven','Crane',
  'Heron','Swan','Dove','Jay','Wren','Finch','Lark','Robin',
  'Sparrow','Starling','Martin','Merlin','Phoenix','Gryphon',
  'Otter','Marten','Lynx',
  'Flint','Jade','Opal','Onyx','Mica','Beryl','Topaz','Garnet',
  'Ruby','Perl','Coral','Ivory','Jet','Agate','Amber','Crystal','Gem','Slate',
  'Gray','Blue','Cyan','Teal','Navy','Indigo','Azure','Cobalt',
  'Olive','Sage','Moss','Amber','Rust','Sienna','Ochre','Umber',
  'Ash','Onyx','Ivory','Hazel',
  'Spring','Autumn','Summer','Winter','April','May','June','July','August',
  'Vernal','Equinox','Solstice',
]);

const EN_CLASSIC = new Set([
  'Alex','Avery','Blair','Blake','Casey','Corey','Dana','Darcy',
  'Devon','Drew','Ellis','Emery','Erin','Gale','Harley','Haven',
  'Hollis','Jamie','Jesse','Jody','Jordan','Jules','Kelly','Kerry',
  'Lane','Lee','Logan','Lynn','Marley','Morgan','Nico','Noel',
  'Pat','Quinn','Ray','Reese','Remy','Rene','Ricki','Riley',
  'Robin','Rowan','Sam','Sandy','Shawn','Skyler','Sloan','Stacy',
  'Sunny','Taylor','Terry','Toni','Tracy','Val','Vern','Wynn',
  'Carey','Finley','Ariel','Payton',
]);

const EN_MODERN = new Set([
  'Arden','Aris','Auden','Briar','Cam','Cyan','Ember','Greer',
  'Indigo','Jade','Juno','Kai','Kit','Lennox','Lux','Merit',
  'Milan','Nova','Onyx','Perry','Quincy','Rory','Rumi','Salem',
  'Shiloh','Sidney','Tate','Tatum','Teal','True','Vesper',
  'Winter','Oren','Sorin','Sable','Halden','Linden','Arlo',
  'Beck','Brett','Channing','Cleo','Corrie','Darby','Devin',
  'Harlow','Hayden','Jalen','Keegan','Kirby','Lane','Nevada',
  'Oakley','Parker','Reagan','Rory','Rowan','Sasha','Tierney',
]);

// ═══════════════════════════════════════════════════════════════
// 内部工具
// ═══════════════════════════════════════════════════════════════

function pick<T>(arr: T[], k: number): T[] {
  // Fisher-Yates on a copy, take first k
  const copy = [...arr];
  for (let i = copy.length - 1; i > 0 && k > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j] as T, copy[i] as T];
  }
  return copy.slice(0, k);
}

function pickOne<T>(arr: T[]): T {
  const el = arr[Math.floor(Math.random() * arr.length)];
  return el as T;
}

// ═══════════════════════════════════════════════════════════════
// 公共 API
// ═══════════════════════════════════════════════════════════════

export function genCn(givenOnly = false): string {
  if (givenOnly) {
    const n = Math.random() < 0.5 ? 2 : 3;
    return pick(GIVEN_CHARS_DEDUPED, n).join('');
  }
  const surname = pickOne(SURNAMES);
  const nGiven = Math.random() < 0.5 ? 1 : 2;
  const given = pick(GIVEN_CHARS_DEDUPED, nGiven);
  return surname + given.join('');
}

export type EnMode = 'nature' | 'classic' | 'modern' | 'any';

export function genEn(maxLen = 7, mode: EnMode = 'any'): string {
  let pool: string[];
  if (mode === 'nature') pool = EN_NAMES.filter((n) => EN_NATURE.has(n));
  else if (mode === 'classic') pool = EN_NAMES.filter((n) => EN_CLASSIC.has(n));
  else if (mode === 'modern') pool = EN_NAMES.filter((n) => EN_MODERN.has(n));
  else pool = [...EN_NAMES];

  const filtered = pool.filter((n) => n.length <= maxLen);
  return pickOne(filtered.length > 0 ? filtered : pool);
}

export function genPair(): { cn: string; en: string } {
  return { cn: genCn(), en: genEn() };
}

export function* genBatch(n = 10, givenOnly = false, enMode: EnMode = 'any'): Generator<{ cn: string; en: string }> {
  for (let i = 0; i < n; i++) {
    yield { cn: genCn(givenOnly), en: genEn(7, enMode) };
  }
}

// Stats for reporting
export const STATS = {
  surnameCount: SURNAMES.length,
  givenCharCount: GIVEN_CHARS_DEDUPED.length,
  enNameCount: EN_NAMES.length,
};
