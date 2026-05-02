#!/usr/bin/env python3
"""
mega_migrate.py — Mega 大体 product migration script (v2).

DIAGNOSTIC (read-only):   python3 mega_migrate.py
MIGRATE    (writes DB):    python3 mega_migrate.py --run

Each product has an explicit 'sku' field:
  'SPxxxxx'  → update that exact DB row
  None       → insert as new product
"""

import sqlite3, os, sys, argparse
sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'popcore.db')

# DB format uses a space: '大娃 1000%' / '大娃 400%'
# 招财猫 figurines get a new type since none exists yet
ZHAOICAIMAO_TYPE = '大娃手办'

# ---------------------------------------------------------------------------
# PRODUCTS — 66 total
# sku=None  → INSERT as new product
# sku='SP…' → UPDATE that existing row (product_type, release_date, price,
#              edition_size, notes only — ip_series left unchanged)
# ---------------------------------------------------------------------------
PRODUCTS = [
    # ── A: Mega Space Molly 1000% ───────────────────────────────────────────
    dict(sku=None,      cn='地球的女儿',       en='The Girl from the Earth',
         type='大娃 1000%', ip='Molly',         release='2021-04-01', price=14000.0, edition='305',
         notes='首发产品，具有最高收藏价值。发售前NFC认证尚未推出。存在400%版本。'),
    dict(sku='SP00558', cn='Keith Haring',    en='Keith Haring',
         type='大娃 1000%', ip='Molly',         release='2021-10-01', price=1400.0,  edition='3500',
         notes='首次与艺术家Keith Haring合作。全身大胆色彩，胸口和枪上有艺术家签名，满身涂鸦细节。无400%版本。'),
    dict(sku='SP00564', cn='Moncler',         en='Moncler',
         type='大娃 1000%', ip='Molly',         release='2022-01-01', price=6000.0,  edition='2000',
         notes='奢侈品牌联名，经典黑白灰色调，轻盈简约风格。无400%版本。'),
    dict(sku='SP00561', cn='大久保',           en='Instinctoy',
         type='大娃 1000%', ip='Molly',         release='2022-03-01', price=2500.0,  edition='3000',
         notes='日本艺术家联名，透明电镀工艺，黑白磁吸生物配件。存在400%版本。瑕疵品售价$1500。'),
    # #05 — only blind-box "Zsiga Care Bears" matched; Mega 1000% Care-A-Lot Bear is missing
    dict(sku=None,      cn='爱心熊',           en='Care-A-Lot Bear',
         type='大娃 1000%', ip='Molly',         release='2022-11-01', price=1200.0,  edition='2000',
         notes='40周年限定配色，冰淇淋色系，金属连接件，气囊内填充彩色毛绒星形填充物。存在400%版本（10000体）。无盒含卡售价。'),
    dict(sku='SP00565', cn='美林的礼物',        en='Meilin Panda 1000%',
         type='大娃 1000%', ip='Molly',         release='2023-01-01', price=2500.0,  edition='2000',
         notes='艺术家韩美林联名，半透明设计，水转印工艺，反光条散射光线。存在400%版本（实体造型）。无盒含卡售价。'),
    dict(sku='SP00557', cn='心语',             en='Heartleft Words',
         type='大娃 1000%', ip='Molly',         release='2024-02-01', price=1900.0,  edition='',
         notes='情人节限定，机身内填充毛绒爱心，发光爱心内核TYPE-C充电。粉色特别版比例9:1。存在400%版本（特别版比例6:1）。'),
    dict(sku='SP00566', cn='路易斯',           en='Louis De Guzman',
         type='大娃 1000%', ip='Molly',         release='2024-03-01', price=3600.0,  edition='999',
         notes='芝加哥视觉艺术家联名，腹部填充EVA几何积木，气囊内流动珊瑚砂。存在400%版本。'),
    dict(sku='SP00560', cn='迪迦奥特曼',        en='Ultraman Tiga',
         type='大娃 1000%', ip='Molly',         release='2024-05-01', price=1800.0,  edition='2000',
         notes='经典红蓝配色，手持发光武器，胸口磁吸件可发光（蓝色常亮，2分钟后切换红色闪烁）。存在400%版本（无发光）。'),
    dict(sku='SP00562', cn='兰博基尼',          en='Lamborghini 1000%',
         type='大娃 1000%', ip='Molly',         release='2024-06-01', price=4000.0,  edition='1500',
         notes='第二代（首款绿色款之后），经典黄黑配色，气囊内填充轮胎。无400%版本。'),
    # #11 — confirmed missing
    dict(sku=None,      cn='唐老鸭&戴斯',       en='Donald Duck & Daisy Duck',
         type='大娃 1000%', ip='Molly',         release='2024-08-01', price=1500.0,  edition='',
         notes='迪士尼90周年联名，水手帽和领结印在头部，磁吸可拆卸鸭嘴和蝴蝶结。存在400%版本。'),
    dict(sku='SP00577', cn='百乐门',           en='Palmer House 1000%',
         type='大娃 1000%', ip='Molly',         release='2024-08-01', price=1800.0,  edition='',
         notes='第二款上海城市文化发售，霓虹紫色调，气囊按键开关流水灯。另有投影版本含小型投影枪（5个上海地标图案）。存在400%版本（无发光）。标准版。'),
    dict(sku='SP00556', cn='史迪奇',           en='Stitch',
         type='大娃 1000%', ip='Molly',         release='2024-12-01', price=1800.0,  edition='4000',
         notes='经典大耳朵磁吸可拆卸，可爱蓝色外星人造型，面部遮罩还原经典表情。存在400%版本。'),
    dict(sku='SP00573', cn='Jon Burgerman',   en='Jon Burgerman 1000%',
         type='大娃 1000%', ip='Molly',         release='2025-04-01', price=3600.0,  edition='2500',
         notes='英国涂鸦艺术家联名，唯一蓝鼻子Molly，多巴胺色彩，腹部填充彩色毛绒球，经典镭射眼，可拆卸围巾附6个磁吸彩色球。存在400%版本。'),

    # ── B: Mega Royal Molly 1000% ───────────────────────────────────────────
    # #15 — SP00462 is the 400% version; 1000% not found in DB → insert
    dict(sku=None,      cn='诞生公主蓝',        en='Original Princess Blue',
         type='大娃 1000%', ip='Molly',         release='2023-09-01', price=4000.0,  edition='699',
         notes='泡泡玛特城市乐园开幕纪念，首款Royal Molly公主，欧式公主风，透明蓝色条纹裙搭配蓝色暗纹花，磁吸皇冠。存在400%版本。'),
    dict(sku='SP00576', cn='蜷川实花',          en='Mika Ninagawa Royal Molly',
         type='大娃 1000%', ip='Molly',         release='2024-01-01', price=6600.0,  edition='999',
         notes='日本一线摄影师联名，以其摄影作品为设计来源，首款全透明公主（全身透明），水转印+电镀工艺。存在400%版本。'),
    dict(sku='SP00568', cn='童心',             en='Childlike',
         type='大娃 1000%', ip='Molly',         release='2024-01-01', price=6000.0,  edition='',
         notes='【员工专属，不对外发售】泡泡玛特5周年员工限定，全身卡通涂鸦，反映公司创立初心——保持童心。'),
    dict(sku='SP00570', cn='莫奈睡莲',          en='Monet Les Nympheas',
         type='大娃 1000%', ip='Molly',         release='2025-02-01', price=2500.0,  edition='2999',
         notes='波士顿美术馆联名，重新诠释莫奈《睡莲》名作，石墨烯工艺还原油画质感，半透明皇冠。存在400%版本（$482.9）。'),
    dict(sku='SP00567', cn='白雪公主',          en='Snow White',
         type='大娃 1000%', ip='Molly',         release='2025-06-01', price=700.0,   edition='5000',
         notes='首款迪士尼公主联名，复古童话风，正面苹果造型（USB充电可发光），苹果内有公主剪影，磁吸公主胸针。存在400%版本（无发光，$482.9）。售价为瑕疵品价格。'),

    # ── C: Mega Space Molly 400% ────────────────────────────────────────────
    dict(sku='SP00447', cn='西瓜',             en='Watermelon',
         type='大娃 400%',  ip='Molly',         release='2021-08-01', price=421.9,   edition='',
         notes='红绿西瓜配色，太空服重现西瓜皮深浅变化，头部/手套/鞋重现红色果肉，头部印有西瓜子。'),
    dict(sku=None,      cn='夏日特调系列',       en='Soft Drinks Series',
         type='大娃 400%',  ip='Molly',         release='2022-04-01', price=633.9,   edition='',
         notes='6+1配置：莫吉托、粉红女士、曼哈顿、天使之吻、蓝色夏威夷、毛伊岛莫斯科骡子，隐藏款彩虹天堂（1:18比例）。无1000%或100%版本。'),
    # #22 — SP00455 is correct 400% Meilin Panda (name uses 熊猫 not 礼物)
    dict(sku='SP00455', cn='美林的礼物',        en='Meilin Panda 400%',
         type='大娃 400%',  ip='Molly',         release='',           price=680.0,   edition='',
         notes='实体造型，水转印工艺。艺术家韩美林联名400%版本。'),
    dict(sku='SP00442', cn='易怒熊',           en='Grumpy Bear',
         type='大娃 400%',  ip='Molly',         release='2023-03-01', price=633.9,   edition='',
         notes='浅蓝色配金属连接件，主题为表达所有情绪。'),
    dict(sku='SP00443', cn='派大星',           en='Patrick Star',
         type='大娃 400%',  ip='Molly',         release='2023-05-01', price=421.9,   edition='',
         notes='海绵宝宝联名，脸部印有经典表情，连接件从粉色渐变至透明。'),
    dict(sku='SP00439', cn='兰博基尼',          en='Lamborghini 400%',
         type='大娃 400%',  ip='Molly',         release='2023-09-01', price=1199.0,  edition='',
         notes='首款品牌联名，经典绿色牛头配色，气囊内填充轮胎。同期1000%版本（1500体）附带小型玩具车。第一代兰博基尼400%版本。'),
    dict(sku='SP00434', cn='搜特吉',           en='Solestage',
         type='大娃 400%',  ip='Molly',         release='2023-10-01', price=421.9,   edition='',
         notes='北美唐人街精品店联名，透明洛杉矶日落渐变，眼睛内有棕榈树海滩，首次枪内填充毛绒。'),
    dict(sku=None,      cn='星球系列',          en='Planet Series',
         type='大娃 400%',  ip='Molly',         release='2023-10-01', price=401.9,   edition='',
         notes='6+2配置：木星、土星、海王星、水星、金星、火星，隐藏款天王星（1:12）、地球（1:24）。无1000%或100%版本。'),
    dict(sku=None,      cn='飞天小女警-泡泡',    en='PowerPuff Girl Bubble',
         type='大娃 400%',  ip='Molly',         release='2024-06-01', price=482.9,   edition='',
         notes='泡泡脸部还原卡通设计，黄蓝配色。存在100%配对版本。'),
    dict(sku=None,      cn='飞天小女警-花花',    en='PowerPuff Girl Blossom',
         type='大娃 400%',  ip='Molly',         release='2024-06-01', price=482.9,   edition='',
         notes='花花脸部搭配红色蝴蝶结，橙红粉配色。存在100%配对版本。'),
    # #30 — 32 wrong candidates (all SkullPanda/other); Space Molly Panda missing
    dict(sku=None,      cn='熊猫',             en='Space Molly Panda',
         type='大娃 400%',  ip='Molly',         release='2024-07-01', price=421.9,   edition='',
         notes='成都大熊猫基地（华华）联名，全身毛绒，胸口印Molly和大熊猫基地Logo，做旧气囊内填充苹果，太空相机替换为竹子。无1000%或100%版本。'),
    dict(sku=None,      cn='可口可乐3.0',       en='Coca Cola 3.0',
         type='大娃 400%',  ip='Molly',         release='2024-07-01', price=421.9,   edition='',
         notes='巴黎奥运会联名，红白配色，胸口奥运五环彩色可口可乐Logo，头盔印法语"享受快乐"。无1000%版本。'),
    dict(sku=None,      cn='中国女子篮球',       en="China Women's Basketball Team",
         type='大娃 400%',  ip='Molly',         release='2024-07-01', price=421.9,   edition='',
         notes='无球衣卡和含球衣卡版本（5:1生产比例），纪念球衣碎片卡。存在100%版本，无1000%版本。'),
    dict(sku='SP00445', cn='葡挞',             en='Eggtart',
         type='大娃 400%',  ip='Molly',         release='2024-08-01', price=499.0,   edition='',
         notes='澳门限定，焦糖太空服搭配葡式蛋挞脸部，气囊内填充蛋挞。无1000%或100%版本。'),
    dict(sku='SP00456', cn='百乐门',           en='Palmer House 400%',
         type='大娃 400%',  ip='Molly',         release='2024-09-01', price=421.9,   edition='',
         notes='上海旗舰店2周年庆，第二代，霓虹紫色调搭配百乐门Logo，复古海派风格。地点限定400%版本。'),
    dict(sku='SP00449', cn='小黄人',           en='Minion',
         type='大娃 400%',  ip='Molly',         release='2024-09-01', price=421.9,   edition='',
         notes='经典黄色搭配蓝色工装裤，还原经典表情，头部胡茬细节。无1000%或100%版本。'),
    dict(sku='SP00437', cn='珍珠奶茶',          en='Bubble Tea',
         type='大娃 400%',  ip='Molly',         release='2024-09-01', price=599.0,   edition='',
         notes='台湾地区限定，可可色调，珍珠眼睛，腹部和脚部全填充珍珠。无1000%或100%版本。'),
    dict(sku='SP00433', cn='小丑',             en='Joker',
         type='大娃 400%',  ip='Molly',         release='2024-10-01', price=421.9,   edition='',
         notes='小丑经典紫绿配色，烟熏眼妆细节，面罩掀开露出经典小丑笑脸，DC电影《小丑2》联动。无100%版本。'),
    dict(sku='SP00432', cn='三丽鸥联名',        en='Sanrio Characters Series',
         type='大娃 400%',  ip='Molly',         release='2024-10-01', price=401.9,   edition='',
         notes='6+1配置：肉桂狗、布丁狗、酷洛米、美乐蒂、帕恰狗、Hello Kitty，隐藏款汉顿（1:18比例）。无1000%或100%版本。'),
    dict(sku='SP00448', cn='心悦',             en='Smitten Love',
         type='大娃 400%',  ip='Molly',         release='2025-01-01', price=421.9,   edition='',
         notes='第三款情人节限定，奶粉色调搭配猫眼光效，全身填充毛绒爱心。'),
    dict(sku='SP00438', cn='甜梦熊',           en='Sweet Dreams Bear',
         type='大娃 400%',  ip='Molly',         release='2025-02-01', price=482.9,   edition='',
         notes='"晚安，好梦"主题，浅紫色搭配金属连接件。存在100%配对版本。'),
    dict(sku='SP00444', cn='擎天柱',           en='Optimus Prime',
         type='大娃 400%',  ip='Molly',         release='2025-03-01', price=421.9,   edition='',
         notes='经典红蓝汽车人配色及汽车人Logo，透明面罩露出Molly眼睛。'),
    dict(sku='SP00340', cn='Jon Burgerman',   en='Jon Burgerman 400%',
         type='大娃 400%',  ip='Molly',         release='2025-04-01', price=421.9,   edition='',
         notes='英国涂鸦艺术家联名400%版本，附3个磁吸彩色球（1000%版本为6个）。'),
    dict(sku='SP00446', cn='摩卡',             en='Mocha',
         type='大娃 400%',  ip='Molly',         release='2025-05-01', price=521.0,   edition='',
         notes='成都SKP门店限定，哑光质感搭配液体流动橡胶工艺，大理石般纹理，每件独一无二。无1000%或100%版本。'),
    dict(sku='SP00383', cn='榴莲人',           en='Durian Man',
         type='大娃 400%',  ip='Molly',         release='2025-05-01', price=421.9,   edition='',
         notes='马来西亚艺术家Radio Woon（LALA COMPANY）联名，黑白配荧光绿点缀，全身毛绒，头部可拆卸，白色脸（非标准），气囊内有榴莲情绪，脚趾上色细节。无1000%版本。'),
    dict(sku='SP00436', cn='Rainbow 2.0',     en='Rainbow 2.0',
         type='大娃 400%',  ip='Molly',         release='2025-06-01', price=421.9,   edition='',
         notes='港澳地区抽签加海外门店发售，电镀工艺，连接件和气囊彩虹渐变，机身银色镭射光泽。'),
    # #46 — SP00133 is Labubu Vans, not Space Molly VANS → insert new
    dict(sku=None,      cn='VANS',             en='Space Molly VANS',
         type='大娃 400%',  ip='Molly',         release='2025-06-01', price=421.9,   edition='',
         notes='粉蓝渐变面罩，双手不同（肉色+黑色布手套），机身和气囊经典VANS黑白棋盘格，全身滑板涂鸦。'),
    dict(sku='SP00366', cn='咱们裸熊',          en='We Bare Bears',
         type='大娃 400%',  ip='Molly',         release='2025-09-01', price=421.9,   edition='',
         notes='全身高密度毛绒，背面"We Bare Bears"刺绣Logo，3款：格里（棕熊）、熊猫、冰熊（1:1:1比例，无隐藏款）。'),

    # ── D: Mega Labubu ──────────────────────────────────────────────────────
    # #48 — SP00104 is blind box "我们是星星人" → wrong match → insert
    dict(sku=None,      cn='我们',             en='All About Us',
         type='大娃 1000%', ip='Labubu',        release='2024-03-01', price=5000.0,  edition='1699',
         notes='情人节限定，首款使用毛绒材质，粉色心形眼睛，左胸有磁吸Tycoco（男友）胸章。触摸额头中央NFC认证。无400%版本。'),
    # #49 — SP00400 is the 400% version; 1000% ($4000, 1999体) is a separate product → insert
    dict(sku=None,      cn='素描',             en='Sketch Labubu 1000%',
         type='大娃 1000%', ip='Labubu',        release='2024-10-01', price=4000.0,  edition='1999',
         notes='模仿艺术家龙家升速写风格，机身使用速写纸质感哑光工艺，如同3D速写作品。触摸额头中央NFC认证。存在400%版本（无数量限制）。'),
    dict(sku='SP00400', cn='素描',             en='Sketch Labubu 400%',
         type='大娃 400%',  ip='Labubu',        release='2024-10-01', price=421.9,   edition='',
         notes='模仿艺术家龙家升速写风格，机身使用速写纸质感哑光工艺，如同3D速写作品。400%版本，无数量限制。'),
    dict(sku='SP00594', cn='圣诞',             en='Winter Holiday',
         type='大娃 1000%', ip='Labubu',        release='2025-11-01', price=2100.0,  edition='',
         notes='圣诞限定，全身毛绒材质，两款磁吸配件（红鼻子+冬青花环），冬青花环附白色Labubu挂件。触摸额头中央NFC认证。无400%版本。'),

    # ── E: Mega Collection SkullPanda ───────────────────────────────────────
    dict(sku='SP00574', cn='融',              en='Thaw',
         type='大娃 1000%', ip='SkullPanda',    release='2022-05-01', price=1800.0,  edition='2999',
         notes='全身珍珠光泽釉面涂层，可拆卸面罩，可动手臂，头后部两张脸贴在一起，主题：在"矛盾"中认识自己。触摸左脚后跟NFC认证。无400%版本。'),
    dict(sku='SP00460', cn='埃贡·席勒',        en='Egon Schiele',
         type='大娃 400%',  ip='SkullPanda',    release='2024-09-01', price=421.9,   edition='5000',
         notes='波士顿艺术博物馆联名，全身速写纸质感石头漆（复原原作），可拆卸面罩，可动手臂。面罩结合席勒自画像，机身展现《蹲伏的女人》画作。'),
    dict(sku='SP00458', cn='红水晶',           en='Red Crystal',
         type='大娃 400%',  ip='SkullPanda',    release='2025-05-01', price=421.9,   edition='',
         notes='整体透明红色如水晶，可拆卸面罩，可动手臂，全身及面罩分布不规则黑色分块线条，黑色区域为浮雕水转印3D立体呈现，腿部嵌入红色水晶夜光效果。'),
    dict(sku=None,      cn='梵高博物馆·向日葵', en='Van Gogh Museum Sunflower 1000%',
         type='大娃 1000%', ip='SkullPanda',    release='2025-08-01', price=2200.0,  edition='2499',
         notes='全身石头漆涂层，半透明哑光面罩边缘有向日葵水转印，机身印有梵高致弟弟西奥的信件，附9个磁吸向日葵配件（1000%版），整体复古感充满向日葵活力。'),
    # #56 — SP00459 "Skull Panda 400% 梵高向日葵" found in ambiguous list for #30
    dict(sku='SP00459', cn='梵高博物馆·向日葵', en='Van Gogh Museum Sunflower 400%',
         type='大娃 400%',  ip='SkullPanda',    release='2025-08-01', price=482.9,   edition='',
         notes='全身石头漆涂层，半透明哑光面罩边缘有向日葵水转印，机身印有梵高致弟弟西奥的信件，附4个磁吸向日葵配件（400%版），整体复古感充满向日葵活力。'),

    # ── F: Mega Just Dimoo ──────────────────────────────────────────────────
    dict(sku='SP00571', cn='让·米歇尔·巴斯奎特', en='Michel Basquiat',
         type='大娃 1000%', ip='Dimoo',         release='2022-05-01', price=2000.0,  edition='',
         notes='与艺术家巴斯奎特合作，街头艺术代表。全白冷白Dimoo覆盖巴斯奎特涂鸦。头顶皇冠元素及"New York USA"象征巴斯奎特出生地。正面"Jack Johnson"，背面"Jersey Joe Walcott"（著名黑人拳击手，巴斯奎特灵感来源）。无400%版本。'),
    dict(sku='SP00572', cn='雷阵雨',           en='Thunder Shower 1000%',
         type='大娃 1000%', ip='Dimoo',         release='2024-07-01', price=1900.0,  edition='1299',
         notes='头顶闪电可磁感应发光，附6个独立配件，黑皮肤Dimoo金色眼线。触摸头部哭泣表情NFC认证。存在400%版本（4件配件，无发光）。'),
    dict(sku='SP00575', cn='雷阵雨',           en='Thunder Shower 400%',
         type='大娃 400%',  ip='Dimoo',         release='2024-07-01', price=590.0,   edition='',
         notes='黑皮肤Dimoo金色眼线，附4个配件，无发光功能。400%版本。'),
    dict(sku=None,      cn='蜷川实花',          en='Mika Ninagawa Dimoo 400%',
         type='大娃 400%',  ip='Dimoo',         release='2023-02-01', price=790.0,   edition='',
         notes='首款Dimoo Mega系列弯臂造型，珍珠细闪设计，花卉图案，附4个磁吸蝴蝶配件（仿真），主题："花入梦，随梦成长"。存在1000%版本。'),
    dict(sku=None,      cn='熊本熊',           en='Kumanon',
         type='大娃 400%',  ip='Dimoo',         release='2023-08-01', price=400.0,   edition='',
         notes='全身手感漆材质，三款磁吸配件（2耳+1尾），可摆动手臂，可旋转头部，云朵脑袋搭配熊本熊经典表情。无1000%版本。'),
    dict(sku=None,      cn='泽',              en='Rejuvenating',
         type='大娃 400%',  ip='Dimoo',         release='2024-02-01', price=1100.0,  edition='200',
         notes='洛杉矶Westfield Century City门店独家发售，使用头卡包装设计（与常规不同），云朵脑袋融合沙漠与绿洲及两棵仙人掌，水滴设计为沙漠带来生机。无1000%版本。'),
    # #63 — SP00341 is Dimoo Mickey backpack (周边), not the Mega figure → insert
    dict(sku=None,      cn='米奇',             en='Mickey Mouse Dimoo',
         type='大娃 400%',  ip='Dimoo',         release='2025-03-01', price=421.9,   edition='',
         notes='还原米奇经典红色工装裤和黄鞋+白手套，5个磁吸配件（耳朵发箍+红色米奇头胸章+蓝色音符+蓝色米奇Logo+黄色Dimoo Logo）。存在1000%版本。'),

    # ── G: Dimoo 招财猫手办 ─────────────────────────────────────────────────
    dict(sku=None,      cn='千万两-隐藏款',      en='Maneki Neko Secret',
         type=ZHAOICAIMAO_TYPE, ip='Dimoo',     release='2021-02-01', price=400.0,   edition='',
         notes='招财猫手办隐藏款，普通版为蓝色三花猫。金币为独立配件，可放在Dimoo腹前（非磁吸）。微信盲盒机发售，限时1小时无数量限制。'),
    dict(sku=None,      cn='招财有鱼1.0',       en='Lucky Fish 1.0',
         type=ZHAOICAIMAO_TYPE, ip='Dimoo',     release='2023-09-01', price=1600.0,  edition='1200',
         notes='以招财猫为原型搭配红色坐垫，白金亮漆，猫眼设计，左爪可小幅摆动，金币形鱼刻有"有鱼"（丰收），寓意全年丰盛。触摸头后部NFC认证。上海PTS展发售。'),
    dict(sku=None,      cn='乌鲤沐锦2.0',       en='Black Koi Bath in Brocade 2.0',
         type=ZHAOICAIMAO_TYPE, ip='Dimoo',     release='2024-04-01', price=1600.0,  edition='1200',
         notes='黑金亮漆搭配黑色坐垫，背部锦鲤图案，左爪可小幅摆动，金币形鱼刻有"发财"（致富），寓意财源滚滚。触摸头后部NFC认证。泰国TTE展抽签（200体），后北京PTS展（1000体）发售。'),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_blob(sku, jzm, name, brand, ptype, series):
    return ' '.join(p.lower() for p in [sku or '', jzm or '', name or '',
                                         brand or '', ptype or '', series or ''])

def next_sku(cur):
    row = cur.execute(
        "SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1"
    ).fetchone()
    num = int(row['sku'][2:]) + 1 if row else 1
    return f'SP{num:05d}'

def fetch_by_sku(cur, sku):
    row = cur.execute(
        'SELECT id, sku, name_cn_en, jizhanming, product_type, ip_series, '
        'price, release_date, edition_size, notes FROM products WHERE sku=?', (sku,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------

def run_diagnostic(con):
    cur = con.cursor()
    SEP = '=' * 70
    updates = [(p, fetch_by_sku(cur, p['sku'])) for p in PRODUCTS if p['sku']]
    inserts = [p for p in PRODUCTS if not p['sku']]
    missing_skus = [(p, p['sku']) for p, row in updates if row is None]

    print(SEP)
    print(f'PLANNED ACTIONS  (v2 — explicit SKU overrides)')
    print(SEP)
    print(f'  Updates : {len(updates)}')
    print(f'  Inserts : {len(inserts)}')
    if missing_skus:
        print(f'  WARNING — {len(missing_skus)} override SKU(s) not found in DB:')
        for p, s in missing_skus:
            print(f'    {s}  {p["cn"]}')

    print()
    print('── UPDATES ──────────────────────────────────────────────────────────')
    for p, row in updates:
        if not row:
            print(f'\n  !! SKU {p["sku"]} NOT IN DB — {p["cn"]}')
            continue
        print(f'\n  {p["sku"]}  {row["name_cn_en"]}')
        print(f'    type  : {row["product_type"]!r} → {p["type"]!r}')
        print(f'    date  : {row["release_date"]!r} → {p["release"]!r}')
        print(f'    price : {row["price"]} → {p["price"]}')
        print(f'    edn   : {row["edition_size"]!r} → {p["edition"]!r}')
        print(f'    notes : {"overwrite" if row["notes"] else "set (empty)"}')

    print()
    print('── INSERTS ──────────────────────────────────────────────────────────')
    row = cur.execute(
        "SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1"
    ).fetchone()
    n = int(row['sku'][2:]) + 1 if row else 1
    for p in inserts:
        print(f'  SP{n:05d}  [{p["type"]}]  {p["cn"]} / {p["en"]}')
        print(f'           ip={p["ip"]}  date={p["release"]}  price=${p["price"]}')
        n += 1

    print()
    print('Run with --run to apply.')


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------

def run_migrate(con):
    cur = con.cursor()
    updates = []
    inserts = []
    bad_skus = []

    for p in PRODUCTS:
        if p['sku']:
            row = fetch_by_sku(cur, p['sku'])
            if row:
                updates.append((p, row))
            else:
                bad_skus.append(p)
        else:
            inserts.append(p)

    print(f'Updates: {len(updates)},  Inserts: {len(inserts)}', end='')
    if bad_skus:
        print(f',  BAD SKUs (skipped): {len(bad_skus)}', end='')
        for p in bad_skus:
            print(f'\n  !! {p["sku"]} not found — {p["cn"]}')
    print()

    ans = input('\nProceed? [y/N]: ').strip().lower()
    if ans != 'y':
        print('Aborted.')
        return

    try:
        con.execute('BEGIN')

        for p, row in updates:
            blob = make_blob(row['sku'], row['jizhanming'], row['name_cn_en'],
                             'POP MART', p['type'], row['ip_series'])
            cur.execute(
                'UPDATE products SET product_type=?, release_date=?, price=?, '
                'edition_size=?, notes=?, search_blob=? WHERE id=?',
                (p['type'],
                 p['release'] if p['release'] else row['release_date'],
                 p['price'],
                 p['edition'] if p['edition'] else row['edition_size'],
                 p['notes'],
                 blob,
                 row['id']),
            )

        for p in inserts:
            sku = next_sku(cur)
            jzm = f'{p["cn"]} {p["en"]}'.strip()
            blob = make_blob(sku, jzm, p['cn'], 'POP MART', p['type'], p['ip'])
            cur.execute(
                'INSERT INTO products (sku, name_cn_en, jizhanming, price, '
                'ip_series, product_type, brand, release_date, edition_size, '
                'channel, hidden, style_notes, notes, search_blob) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (sku, p['cn'], jzm, p['price'], p['ip'], p['type'],
                 'POP MART', p['release'], p['edition'],
                 '', '', '', p['notes'], blob),
            )

        con.commit()
        print(f'Done. {len(updates)} updated, {len(inserts)} inserted.')
    except Exception as e:
        con.rollback()
        print(f'ERROR: {e}\nRolled back — no changes made.')
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f'ERROR: {DB_PATH} not found')
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')

    try:
        run_migrate(con) if args.run else run_diagnostic(con)
    finally:
        con.close()
