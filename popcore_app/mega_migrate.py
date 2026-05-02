#!/usr/bin/env python3
"""
mega_migrate.py — Diagnostic and migration script for Mega 大体 products.

DIAGNOSTIC (read-only, run this first on the droplet):
    python3 mega_migrate.py

MIGRATE (writes to DB, previews then prompts y/n):
    python3 mega_migrate.py --run

The live popcore.db must be present at popcore_app/popcore.db.
"""

import sqlite3
import os
import sys
import argparse

sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'popcore.db')

# product_type for 招财猫 figurines — update this constant after diagnostic
# confirms what existing similar products use in the DB
ZHAOICAIMAO_TYPE = '大娃手办'

# ---------------------------------------------------------------------------
# ALL PRODUCTS EXTRACTED FROM PDF (66 total)
# Fields: cn, en, type, ip_series, release (YYYY-MM-01), price (CAD float),
#         edition (str, '' if unknown), brand, notes (full feature description)
# ---------------------------------------------------------------------------
PRODUCTS = [
    # ── SECTION A: Mega Space Molly 1000% ───────────────────────────────────
    {
        'cn': '地球的女儿',
        'en': 'The Girl from the Earth',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2021-04-01',
        'price': 14000.0,
        'edition': '305',
        'brand': 'POP MART',
        'notes': (
            '首发产品，具有最高收藏价值。发售前NFC认证尚未推出。'
            '存在400%版本。'
        ),
    },
    {
        'cn': 'Keith Haring',
        'en': 'Keith Haring',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2021-10-01',
        'price': 1400.0,
        'edition': '3500',
        'brand': 'POP MART',
        'notes': (
            '首次与艺术家Keith Haring合作。全身大胆色彩，胸口和枪上有艺术家签名，'
            '满身涂鸦细节。无400%版本。'
        ),
    },
    {
        'cn': 'Moncler',
        'en': 'Moncler',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2022-01-01',
        'price': 6000.0,
        'edition': '2000',
        'brand': 'POP MART',
        'notes': '奢侈品牌联名，经典黑白灰色调，轻盈简约风格。无400%版本。',
    },
    {
        'cn': '大久保',
        'en': 'Instinctoy',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2022-03-01',
        'price': 2500.0,
        'edition': '3000',
        'brand': 'POP MART',
        'notes': (
            '日本艺术家联名，透明电镀工艺，黑白磁吸生物配件。'
            '存在400%版本。瑕疵品售价$1500。'
        ),
    },
    {
        'cn': '爱心熊',
        'en': 'Care-A-Lot Bear',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2022-11-01',
        'price': 1200.0,
        'edition': '2000',
        'brand': 'POP MART',
        'notes': (
            '40周年限定配色，冰淇淋色系，金属连接件，'
            '气囊内填充彩色毛绒星形填充物。存在400%版本（10000体）。无盒含卡售价。'
        ),
    },
    {
        'cn': '美林的礼物',
        'en': 'Meilin Panda',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2023-01-01',
        'price': 2500.0,
        'edition': '2000',
        'brand': 'POP MART',
        'notes': (
            '艺术家韩美林联名，半透明设计，水转印工艺，反光条散射光线。'
            '存在400%版本（实体造型）。无盒含卡售价。'
        ),
    },
    {
        'cn': '心语',
        'en': 'Heartleft Words',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-02-01',
        'price': 1900.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '情人节限定，机身内填充毛绒爱心，发光爱心内核TYPE-C充电。'
            '粉色特别版比例9:1。存在400%版本（特别版比例6:1）。'
        ),
    },
    {
        'cn': '路易斯',
        'en': 'Louis De Guzman',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-03-01',
        'price': 3600.0,
        'edition': '999',
        'brand': 'POP MART',
        'notes': (
            '芝加哥视觉艺术家联名，腹部填充EVA几何积木，'
            '气囊内流动珊瑚砂。存在400%版本。'
        ),
    },
    {
        'cn': '迪迦奥特曼',
        'en': 'Ultraman Tiga',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-05-01',
        'price': 1800.0,
        'edition': '2000',
        'brand': 'POP MART',
        'notes': (
            '经典红蓝配色，手持发光武器，胸口磁吸件可发光'
            '（蓝色常亮，2分钟后切换红色闪烁）。存在400%版本（无发光）。'
        ),
    },
    {
        'cn': '兰博基尼',
        'en': 'Lamborghini 1000%',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-06-01',
        'price': 4000.0,
        'edition': '1500',
        'brand': 'POP MART',
        'notes': (
            '第二代（首款绿色款之后），经典黄黑配色，气囊内填充轮胎。无400%版本。'
        ),
    },
    {
        'cn': '唐老鸭&戴斯',
        'en': 'Donald Duck & Daisy Duck',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-08-01',
        'price': 1500.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '迪士尼90周年联名，水手帽和领结印在头部，磁吸可拆卸鸭嘴和蝴蝶结。'
            '存在400%版本。'
        ),
    },
    {
        'cn': '百乐门',
        'en': 'Palmer House 1000%',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-08-01',
        'price': 1800.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '第二款上海城市文化发售，霓虹紫色调，气囊按键开关流水灯。'
            '另有投影版本含小型投影枪（5个上海地标图案）。存在400%版本（无发光）。标准版。'
        ),
    },
    {
        'cn': '史迪奇',
        'en': 'Stitch',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-12-01',
        'price': 1800.0,
        'edition': '4000',
        'brand': 'POP MART',
        'notes': (
            '经典大耳朵磁吸可拆卸，可爱蓝色外星人造型，面部遮罩还原经典表情。'
            '存在400%版本。'
        ),
    },
    {
        'cn': 'Jon Burgerman',
        'en': 'Jon Burgerman 1000%',
        'type': '大娃1000%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-04-01',
        'price': 3600.0,
        'edition': '2500',
        'brand': 'POP MART',
        'notes': (
            '英国涂鸦艺术家联名，唯一蓝鼻子Molly，多巴胺色彩，'
            '腹部填充彩色毛绒球，经典镭射眼，可拆卸围巾附6个磁吸彩色球。存在400%版本。'
        ),
    },

    # ── SECTION B: Mega Royal Molly 1000% ───────────────────────────────────
    {
        'cn': '诞生公主蓝',
        'en': 'Original Princess Blue',
        'type': '大娃1000%',
        'ip_series': 'Mega Royal Molly',
        'release': '2023-09-01',
        'price': 4000.0,
        'edition': '699',
        'brand': 'POP MART',
        'notes': (
            '泡泡玛特城市乐园开幕纪念，首款Royal Molly公主，欧式公主风，'
            '透明蓝色条纹裙搭配蓝色暗纹花，磁吸皇冠。存在400%版本。'
        ),
    },
    {
        'cn': '蜷川实花',
        'en': 'Mika Ninagawa Royal Molly',
        'type': '大娃1000%',
        'ip_series': 'Mega Royal Molly',
        'release': '2024-01-01',
        'price': 6600.0,
        'edition': '999',
        'brand': 'POP MART',
        'notes': (
            '日本一线摄影师联名，以其摄影作品为设计来源，'
            '首款全透明公主（全身透明），水转印+电镀工艺。存在400%版本。'
        ),
    },
    {
        'cn': '童心',
        'en': 'Childlike',
        'type': '大娃1000%',
        'ip_series': 'Mega Royal Molly',
        'release': '2024-01-01',
        'price': 6000.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '【员工专属，不对外发售】泡泡玛特5周年员工限定，'
            '全身卡通涂鸦，反映公司创立初心——保持童心。'
        ),
    },
    {
        'cn': '莫奈睡莲',
        'en': 'Monet Les Nympheas',
        'type': '大娃1000%',
        'ip_series': 'Mega Royal Molly',
        'release': '2025-02-01',
        'price': 2500.0,
        'edition': '2999',
        'brand': 'POP MART',
        'notes': (
            '波士顿美术馆联名，重新诠释莫奈《睡莲》名作，'
            '石墨烯工艺还原油画质感，半透明皇冠。存在400%版本（$482.9）。'
        ),
    },
    {
        'cn': '白雪公主',
        'en': 'Snow White',
        'type': '大娃1000%',
        'ip_series': 'Mega Royal Molly',
        'release': '2025-06-01',
        'price': 700.0,
        'edition': '5000',
        'brand': 'POP MART',
        'notes': (
            '首款迪士尼公主联名，复古童话风，正面苹果造型（USB充电可发光），'
            '苹果内有公主剪影，磁吸公主胸针。存在400%版本（无发光，$482.9）。售价为瑕疵品价格。'
        ),
    },

    # ── SECTION C: Mega Space Molly 400% ────────────────────────────────────
    {
        'cn': '西瓜',
        'en': 'Watermelon',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2021-08-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '红绿西瓜配色，太空服重现西瓜皮深浅变化，'
            '头部/手套/鞋重现红色果肉，头部印有西瓜子。'
        ),
    },
    {
        'cn': '夏日特调系列',
        'en': 'Soft Drinks Series',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2022-04-01',
        'price': 633.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '6+1配置：莫吉托、粉红女士、曼哈顿、天使之吻、蓝色夏威夷、毛伊岛莫斯科骡子，'
            '隐藏款彩虹天堂（1:18比例）。无1000%或100%版本。'
        ),
    },
    {
        'cn': '美林的礼物',
        'en': 'Meilin Panda 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '',
        'price': 680.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': '实体造型，水转印工艺。艺术家韩美林联名400%版本。',
    },
    {
        'cn': '易怒熊',
        'en': 'Grumpy Bear',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2023-03-01',
        'price': 633.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '浅蓝色配金属连接件，主题为表达所有情绪。',
    },
    {
        'cn': '派大星',
        'en': 'Patrick Star',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2023-05-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '海绵宝宝联名，脸部印有经典表情，连接件从粉色渐变至透明。',
    },
    {
        'cn': '兰博基尼',
        'en': 'Lamborghini 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2023-09-01',
        'price': 1199.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '首款品牌联名，经典绿色牛头配色，气囊内填充轮胎。'
            '同期1000%版本（1500体）附带小型玩具车。第一代兰博基尼400%版本。'
        ),
    },
    {
        'cn': '搜特吉',
        'en': 'Solestage',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2023-10-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '北美唐人街精品店联名，透明洛杉矶日落渐变，'
            '眼睛内有棕榈树海滩，首次枪内填充毛绒。'
        ),
    },
    {
        'cn': '星球系列',
        'en': 'Planet Series',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2023-10-01',
        'price': 401.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '6+2配置：木星、土星、海王星、水星、金星、火星，'
            '隐藏款天王星（1:12）、地球（1:24）。无1000%或100%版本。'
        ),
    },
    {
        'cn': '飞天小女警-泡泡',
        'en': 'PowerPuff Girl Bubble',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-06-01',
        'price': 482.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '泡泡脸部还原卡通设计，黄蓝配色。存在100%配对版本。',
    },
    {
        'cn': '飞天小女警-花花',
        'en': 'PowerPuff Girl Blossom',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-06-01',
        'price': 482.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '花花脸部搭配红色蝴蝶结，橙红粉配色。存在100%配对版本。',
    },
    {
        'cn': '熊猫',
        'en': 'Panda',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-07-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '成都大熊猫基地（华华）联名，全身毛绒，胸口印Molly和大熊猫基地Logo，'
            '做旧气囊内填充苹果，太空相机替换为竹子。无1000%或100%版本。'
        ),
    },
    {
        'cn': '可口可乐3.0',
        'en': 'Coca Cola 3.0',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-07-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '巴黎奥运会联名，红白配色，胸口奥运五环彩色可口可乐Logo，'
            '头盔印法语"享受快乐"。无1000%版本。'
        ),
    },
    {
        'cn': '中国女子篮球',
        'en': "China Women's Basketball Team",
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-07-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '无球衣卡和含球衣卡版本（5:1生产比例），纪念球衣碎片卡。'
            '存在100%版本，无1000%版本。'
        ),
    },
    {
        'cn': '葡挞',
        'en': 'Eggtart',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-08-01',
        'price': 499.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': '澳门限定，焦糖太空服搭配葡式蛋挞脸部，气囊内填充蛋挞。无1000%或100%版本。',
    },
    {
        'cn': '百乐门',
        'en': 'Palmer House 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-09-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '上海旗舰店2周年庆，第二代，霓虹紫色调搭配百乐门Logo，复古海派风格。'
            '地点限定400%版本。'
        ),
    },
    {
        'cn': '小黄人',
        'en': 'Minion',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-09-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '经典黄色搭配蓝色工装裤，还原经典表情，头部胡茬细节。无1000%或100%版本。',
    },
    {
        'cn': '珍珠奶茶',
        'en': 'Bubble Tea',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-09-01',
        'price': 599.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': '台湾地区限定，可可色调，珍珠眼睛，腹部和脚部全填充珍珠。无1000%或100%版本。',
    },
    {
        'cn': '小丑',
        'en': 'Joker',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-10-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '小丑经典紫绿配色，烟熏眼妆细节，面罩掀开露出经典小丑笑脸，'
            'DC电影《小丑2》联动。无100%版本。'
        ),
    },
    {
        'cn': '三丽鸥联名',
        'en': 'Sanrio Characters Series',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2024-10-01',
        'price': 401.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '6+1配置：肉桂狗、布丁狗、酷洛米、美乐蒂、帕恰狗、Hello Kitty，'
            '隐藏款汉顿（1:18比例）。无1000%或100%版本。'
        ),
    },
    {
        'cn': '心悦',
        'en': 'Smitten Love',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-01-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '第三款情人节限定，奶粉色调搭配猫眼光效，全身填充毛绒爱心。',
    },
    {
        'cn': '甜梦熊',
        'en': 'Sweet Dreams Bear',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-02-01',
        'price': 482.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '"晚安，好梦"主题，浅紫色搭配金属连接件。存在100%配对版本。',
    },
    {
        'cn': '擎天柱',
        'en': 'Optimus Prime',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-03-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '经典红蓝汽车人配色及汽车人Logo，透明面罩露出Molly眼睛。',
    },
    {
        'cn': 'Jon Burgerman',
        'en': 'Jon Burgerman 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-04-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': '英国涂鸦艺术家联名400%版本，附3个磁吸彩色球（1000%版本为6个）。',
    },
    {
        'cn': '摩卡',
        'en': 'Mocha',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-05-01',
        'price': 521.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '成都SKP门店限定，哑光质感搭配液体流动橡胶工艺，'
            '大理石般纹理，每件独一无二。无1000%或100%版本。'
        ),
    },
    {
        'cn': '榴莲人',
        'en': 'Durian Man',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-05-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '马来西亚艺术家Radio Woon（LALA COMPANY）联名，黑白配荧光绿点缀，'
            '全身毛绒头部与机身，头部可拆卸，白色脸（非标准），'
            '气囊内有榴莲情绪，脚趾上色细节。无1000%版本。'
        ),
    },
    {
        'cn': 'Rainbow 2.0',
        'en': 'Rainbow 2.0',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-06-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '港澳地区抽签加海外门店发售，电镀工艺，'
            '连接件和气囊彩虹渐变，机身银色镭射光泽。'
        ),
    },
    {
        'cn': 'VANS',
        'en': 'VANS',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-06-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '粉蓝渐变面罩，双手不同（肉色+黑色布手套），'
            '机身和气囊经典VANS黑白棋盘格，全身滑板涂鸦。'
        ),
    },
    {
        'cn': '咱们裸熊',
        'en': 'We Bare Bears',
        'type': '大娃400%',
        'ip_series': 'Mega Space Molly',
        'release': '2025-09-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '全身高密度毛绒，背面"We Bare Bears"刺绣Logo，'
            '3款：格里（棕熊）、熊猫、冰熊（1:1:1比例，无隐藏款）。'
        ),
    },

    # ── SECTION D: Mega Labubu ───────────────────────────────────────────────
    {
        'cn': '我们',
        'en': 'All About Us',
        'type': '大娃1000%',
        'ip_series': 'Mega Labubu',
        'release': '2024-03-01',
        'price': 5000.0,
        'edition': '1699',
        'brand': 'POP MART',
        'notes': (
            '情人节限定，首款使用毛绒材质，粉色心形眼睛，'
            '左胸有磁吸Tycoco（男友）胸章。触摸额头中央NFC认证。无400%版本。'
        ),
    },
    {
        'cn': '素描',
        'en': 'Sketch Labubu 1000%',
        'type': '大娃1000%',
        'ip_series': 'Mega Labubu',
        'release': '2024-10-01',
        'price': 4000.0,
        'edition': '1999',
        'brand': 'POP MART',
        'notes': (
            '模仿艺术家龙家升速写风格，机身使用速写纸质感哑光工艺，如同3D速写作品。'
            '触摸额头中央NFC认证。存在400%版本（无数量限制）。'
        ),
    },
    {
        'cn': '素描',
        'en': 'Sketch Labubu 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Labubu',
        'release': '2024-10-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '模仿艺术家龙家升速写风格，机身使用速写纸质感哑光工艺，如同3D速写作品。'
            '400%版本，无数量限制。'
        ),
    },
    {
        'cn': '圣诞',
        'en': 'Winter Holiday',
        'type': '大娃1000%',
        'ip_series': 'Mega Labubu',
        'release': '2025-11-01',
        'price': 2100.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '圣诞限定，全身毛绒材质，两款磁吸配件（红鼻子+冬青花环），'
            '冬青花环附白色Labubu挂件。触摸额头中央NFC认证。无400%版本。'
        ),
    },

    # ── SECTION E: Mega Collection SkullPanda ───────────────────────────────
    {
        'cn': '融',
        'en': 'Thaw',
        'type': '大娃1000%',
        'ip_series': 'Mega Collection SkullPanda',
        'release': '2022-05-01',
        'price': 1800.0,
        'edition': '2999',
        'brand': 'POP MART',
        'notes': (
            '全身珍珠光泽釉面涂层，可拆卸面罩，可动手臂，'
            '头后部两张脸贴在一起，主题：在"矛盾"中认识自己。'
            '触摸左脚后跟NFC认证。无400%版本。'
        ),
    },
    {
        'cn': '埃贡·席勒',
        'en': 'Egon Schiele',
        'type': '大娃400%',
        'ip_series': 'Mega Collection SkullPanda',
        'release': '2024-09-01',
        'price': 421.9,
        'edition': '5000',
        'brand': 'POP MART',
        'notes': (
            '波士顿艺术博物馆联名，全身速写纸质感石头漆（复原原作），'
            '可拆卸面罩，可动手臂。面罩结合席勒自画像，机身展现《蹲伏的女人》画作。'
            '孤独笔触，个人风格强烈，充满张力与情感。'
        ),
    },
    {
        'cn': '红水晶',
        'en': 'Red Crystal',
        'type': '大娃400%',
        'ip_series': 'Mega Collection SkullPanda',
        'release': '2025-05-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '整体透明红色如水晶，可拆卸面罩，可动手臂，'
            '全身及面罩分布不规则黑色分块线条，黑色区域为浮雕水转印3D立体呈现，'
            '渐变透明面罩，腿部嵌入红色水晶夜光效果。'
        ),
    },
    {
        'cn': '梵高博物馆·向日葵',
        'en': 'Van Gogh Museum Sunflower 1000%',
        'type': '大娃1000%',
        'ip_series': 'Mega Collection SkullPanda',
        'release': '2025-08-01',
        'price': 2200.0,
        'edition': '2499',
        'brand': 'POP MART',
        'notes': (
            '全身石头漆涂层，半透明哑光面罩边缘有向日葵水转印，'
            '机身印有梵高致弟弟西奥的信件，附9个磁吸向日葵配件（1000%版），'
            '整体复古感充满向日葵活力。存在400%版本（$482.9，4个配件）。'
        ),
    },
    {
        'cn': '梵高博物馆·向日葵',
        'en': 'Van Gogh Museum Sunflower 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Collection SkullPanda',
        'release': '2025-08-01',
        'price': 482.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '全身石头漆涂层，半透明哑光面罩边缘有向日葵水转印，'
            '机身印有梵高致弟弟西奥的信件，附4个磁吸向日葵配件（400%版），'
            '整体复古感充满向日葵活力。'
        ),
    },

    # ── SECTION F: Mega Just Dimoo ───────────────────────────────────────────
    {
        'cn': '让·米歇尔·巴斯奎特',
        'en': 'Michel Basquiat',
        'type': '大娃1000%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2022-05-01',
        'price': 2000.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '与艺术家巴斯奎特合作，街头艺术代表。全白冷白Dimoo覆盖巴斯奎特涂鸦。'
            '头顶皇冠元素及"New York USA"象征巴斯奎特出生地。'
            '正面"Jack Johnson"，背面"Jersey Joe Walcott"（著名黑人拳击手，巴斯奎特灵感来源）。'
            '无400%版本。'
        ),
    },
    {
        'cn': '雷阵雨',
        'en': 'Thunder Shower 1000%',
        'type': '大娃1000%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2024-07-01',
        'price': 1900.0,
        'edition': '1299',
        'brand': 'POP MART',
        'notes': (
            '头顶闪电可磁感应发光，附6个独立配件，黑皮肤Dimoo金色眼线。'
            '触摸头部哭泣表情NFC认证。存在400%版本（4件配件，无发光）。'
        ),
    },
    {
        'cn': '雷阵雨',
        'en': 'Thunder Shower 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2024-07-01',
        'price': 590.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': '黑皮肤Dimoo金色眼线，附4个配件，无发光功能。400%版本。',
    },
    {
        'cn': '蜷川实花',
        'en': 'Mika Ninagawa Dimoo 400%',
        'type': '大娃400%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2023-02-01',
        'price': 790.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '首款Dimoo Mega系列弯臂造型，珍珠细闪设计，花卉图案，'
            '附4个磁吸蝴蝶配件（仿真），主题："花入梦，随梦成长"。存在1000%版本。'
        ),
    },
    {
        'cn': '熊本熊',
        'en': 'Kumanon',
        'type': '大娃400%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2023-08-01',
        'price': 400.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '全身手感漆材质，三款磁吸配件（2耳+1尾），可摆动手臂，可旋转头部，'
            '云朵脑袋搭配熊本熊经典表情。无1000%版本。'
        ),
    },
    {
        'cn': '泽',
        'en': 'Rejuvenating',
        'type': '大娃400%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2024-02-01',
        'price': 1100.0,
        'edition': '200',
        'brand': 'POP MART',
        'notes': (
            '洛杉矶Westfield Century City门店独家发售，使用头卡包装设计（与常规不同），'
            '云朵脑袋融合沙漠与绿洲及两棵仙人掌，水滴设计为沙漠带来生机。无1000%版本。'
        ),
    },
    {
        'cn': '米奇',
        'en': 'Mickey Mouse Dimoo',
        'type': '大娃400%',
        'ip_series': 'Mega Just Dimoo',
        'release': '2025-03-01',
        'price': 421.9,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '还原米奇经典红色工装裤和黄鞋+白手套，'
            '5个磁吸配件（耳朵发箍+红色米奇头胸章+蓝色音符+蓝色米奇Logo+黄色Dimoo Logo）。'
            '存在1000%版本。'
        ),
    },

    # ── SECTION G: Dimoo 招财猫手办 ─────────────────────────────────────────
    {
        'cn': '千万两-隐藏款',
        'en': 'Maneki Neko Secret',
        'type': ZHAOICAIMAO_TYPE,
        'ip_series': 'Dimoo 招财猫',
        'release': '2021-02-01',
        'price': 400.0,
        'edition': '',
        'brand': 'POP MART',
        'notes': (
            '招财猫手办隐藏款，普通版为蓝色三花猫。金币为独立配件，'
            '可放在Dimoo腹前（非磁吸）。微信盲盒机发售，限时1小时无数量限制。'
        ),
    },
    {
        'cn': '招财有鱼1.0',
        'en': 'Lucky Fish 1.0',
        'type': ZHAOICAIMAO_TYPE,
        'ip_series': 'Dimoo 招财猫',
        'release': '2023-09-01',
        'price': 1600.0,
        'edition': '1200',
        'brand': 'POP MART',
        'notes': (
            '以招财猫为原型搭配红色坐垫，白金亮漆，猫眼设计，左爪可小幅摆动，'
            '金币形鱼刻有"有鱼"（丰收），寓意全年丰盛。触摸头后部NFC认证。上海PTS展发售。'
        ),
    },
    {
        'cn': '乌鲤沐锦2.0',
        'en': 'Black Koi Bath in Brocade 2.0',
        'type': ZHAOICAIMAO_TYPE,
        'ip_series': 'Dimoo 招财猫',
        'release': '2024-04-01',
        'price': 1600.0,
        'edition': '1200',
        'brand': 'POP MART',
        'notes': (
            '黑金亮漆搭配黑色坐垫，背部锦鲤图案，左爪可小幅摆动，'
            '金币形鱼刻有"发财"（致富），寓意财源滚滚。触摸头后部NFC认证。'
            '泰国TTE展抽签（200体），后北京PTS展（1000体）发售。'
        ),
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_search_blob(sku, jzm, name, brand, ptype, series):
    parts = [sku or '', jzm or '', name or '', brand or '', ptype or '', series or '']
    return ' '.join(p.lower() for p in parts)


def find_match(cur, p):
    """Return (match_type, result).

    match_type values:
      'exact'   – single row matching name AND expected product_type
      'partial' – single row matching name only (type differs or unset)
      'ambiguous' – multiple rows matched
      'missing' – no rows matched
    result:
      dict for exact/partial, list of dicts for ambiguous, None for missing
    """
    cn = p['cn']
    en = p['en']
    ptype = p['type']

    def row_cols():
        return (
            'id, sku, name_cn_en, jizhanming, product_type, ip_series, '
            'price, release_date, edition_size, notes'
        )

    # Strategy 1: name match + type already correct
    rows = cur.execute(
        f'SELECT {row_cols()} FROM products '
        'WHERE product_type = ? '
        'AND (name_cn_en LIKE ? OR name_cn_en LIKE ? '
        '     OR jizhanming LIKE ? OR search_blob LIKE ?)',
        (ptype, f'%{cn}%', f'%{en}%', f'%{cn}%', f'%{cn}%'),
    ).fetchall()
    if len(rows) == 1:
        return 'exact', dict(rows[0])
    if len(rows) > 1:
        return 'ambiguous', [dict(r) for r in rows]

    # Strategy 2: name match regardless of type
    rows = cur.execute(
        f'SELECT {row_cols()} FROM products '
        'WHERE name_cn_en LIKE ? OR name_cn_en LIKE ? '
        '   OR jizhanming LIKE ? OR search_blob LIKE ?',
        (f'%{cn}%', f'%{en}%', f'%{cn}%', f'%{cn}%'),
    ).fetchall()
    if len(rows) == 0:
        return 'missing', None
    if len(rows) == 1:
        return 'partial', dict(rows[0])

    # Multiple hits – try to narrow by ip_series
    series_rows = [
        r for r in rows
        if p['ip_series'].lower() in (r['ip_series'] or '').lower()
        or (r['ip_series'] or '').lower() in p['ip_series'].lower()
    ]
    if len(series_rows) == 1:
        return 'partial', dict(series_rows[0])

    return 'ambiguous', [dict(r) for r in rows]


def next_sku(cur):
    row = cur.execute(
        "SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1"
    ).fetchone()
    num = int(row['sku'][2:]) + 1 if row else 1
    return f'SP{num:05d}'


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------

def run_diagnostic(con):
    cur = con.cursor()
    SEP = '=' * 72

    # 1. product_type values
    print(SEP)
    print('1. ALL DISTINCT product_type VALUES IN DB')
    print(SEP)
    rows = cur.execute(
        'SELECT product_type, COUNT(*) cnt FROM products '
        'GROUP BY product_type ORDER BY cnt DESC'
    ).fetchall()
    for r in rows:
        print(f'  {repr(r["product_type"]):30s}  ({r["cnt"]} products)')

    # 2. ip_series storage
    print()
    print(SEP)
    print('2. ip_series FIELD — plain TEXT column, no FK. Sample values:')
    print(SEP)
    rows = cur.execute(
        "SELECT DISTINCT ip_series FROM products "
        "WHERE ip_series IS NOT NULL AND ip_series != '' "
        "ORDER BY ip_series LIMIT 25"
    ).fetchall()
    for r in rows:
        print(f'  {r["ip_series"]}')

    # 3. Sample existing Mega / large-figure products
    print()
    print(SEP)
    print('3. SAMPLE EXISTING MEGA / 大娃 PRODUCTS (up to 5)')
    print(SEP)
    mega = cur.execute(
        "SELECT sku, name_cn_en, jizhanming, price, ip_series, product_type, "
        "       release_date, edition_size, notes "
        "FROM products "
        "WHERE product_type LIKE '%大娃%' OR product_type LIKE '%1000%' "
        "   OR product_type LIKE '%400%' "
        "   OR name_cn_en LIKE '%1000%%' OR name_cn_en LIKE '%400%%' "
        "LIMIT 5"
    ).fetchall()
    if not mega:
        mega = cur.execute(
            "SELECT sku, name_cn_en, jizhanming, price, ip_series, product_type, "
            "       release_date, edition_size, notes "
            "FROM products "
            "WHERE product_type NOT IN ('盲盒', '') AND product_type IS NOT NULL "
            "LIMIT 5"
        ).fetchall()
    if mega:
        for r in mega:
            print(f'\n  SKU:          {r["sku"]}')
            print(f'  name_cn_en:   {r["name_cn_en"]}')
            print(f'  jizhanming:   {r["jizhanming"]}')
            print(f'  price:        {r["price"]}')
            print(f'  ip_series:    {r["ip_series"]}')
            print(f'  product_type: {r["product_type"]}')
            print(f'  release_date: {r["release_date"]}')
            print(f'  edition_size: {r["edition_size"]}')
            print(f'  notes:        {repr((r["notes"] or "")[:80])}')
    else:
        print('  No Mega/大娃 products found yet.')

    # 4. Cross-reference
    print()
    print(SEP)
    print('4. CROSS-REFERENCE: PDF PRODUCTS vs DATABASE')
    print(SEP)

    results = []
    for i, p in enumerate(PRODUCTS):
        mtype, result = find_match(cur, p)
        results.append((i + 1, p, mtype, result))

    for idx, p, mtype, result in results:
        label = f'#{idx:02d} [{p["type"]}] {p["cn"]} / {p["en"]}'
        if mtype == 'exact':
            tag = 'FOUND_EXACT  '
            print(f'  {tag}  {label}')
            print(f'               → SKU {result["sku"]} | '
                  f'type={result["product_type"]} | name={result["name_cn_en"]}')
        elif mtype == 'partial':
            tag = 'FOUND_PARTIAL'
            print(f'  {tag}  {label}')
            print(f'               → SKU {result["sku"]} | '
                  f'type={result["product_type"]} | name={result["name_cn_en"]}')
        elif mtype == 'ambiguous':
            tag = 'AMBIGUOUS    '
            print(f'  {tag}  {label}')
            for r in result:
                print(f'               → SKU {r["sku"]} | '
                      f'type={r["product_type"]} | name={r["name_cn_en"]}')
        else:
            print(f'  MISSING        {label}')

    # 5. Summary
    exact   = [r for r in results if r[2] == 'exact']
    partial = [r for r in results if r[2] == 'partial']
    ambig   = [r for r in results if r[2] == 'ambiguous']
    missing = [r for r in results if r[2] == 'missing']

    print()
    print(SEP)
    print('5. SUMMARY')
    print(SEP)
    print(f'  Total PDF products:           {len(PRODUCTS)}')
    print(f'  Exact matches (type correct): {len(exact)}')
    print(f'  Partial matches (type wrong): {len(partial)}')
    print(f'  Ambiguous (multiple DB rows): {len(ambig)}')
    print(f'  Missing  (will be inserted):  {len(missing)}')

    if missing:
        print()
        print('  Missing products:')
        for _, p, _, _ in missing:
            print(f'    - [{p["type"]}] {p["cn"]} / {p["en"]} '
                  f'(release: {p["release"] or "unknown"}, price: CAD ${p["price"]})')

    if ambig:
        print()
        print('  Ambiguous — manual review needed:')
        for _, p, _, candidates in ambig:
            skus = ', '.join(r['sku'] for r in candidates)
            print(f'    - [{p["type"]}] {p["cn"]}  →  candidates: {skus}')

    print()
    print('Run with --run to apply changes (after reviewing the above).')


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------

def run_migrate(con):
    cur = con.cursor()

    # Build match map; detect duplicate DB-row assignments
    match_results = []
    for p in PRODUCTS:
        mtype, result = find_match(cur, p)
        match_results.append((p, mtype, result))

    # Group by row id to detect collisions (two PDF entries → same DB row)
    row_id_claims = {}
    for p, mtype, result in match_results:
        if mtype in ('exact', 'partial'):
            rid = result['id']
            row_id_claims.setdefault(rid, []).append((p, mtype, result))

    updates = []
    inserts = []
    skipped_ambig = []
    skipped_dup = []
    processed_ids = set()

    for p, mtype, result in match_results:
        if mtype == 'missing':
            inserts.append(p)
        elif mtype == 'ambiguous':
            skipped_ambig.append((p, result))
        else:  # exact or partial
            rid = result['id']
            claims = row_id_claims[rid]
            if len(claims) > 1:
                # Multiple PDF products claim same DB row
                if rid not in processed_ids:
                    # Take the 'exact' claim if any, else first
                    winner = next((c for c in claims if c[1] == 'exact'), claims[0])
                    updates.append((winner[0], winner[2]))
                    processed_ids.add(rid)
                    # Rest become inserts
                    for cp, cm, cr in claims:
                        if cp is not winner[0]:
                            inserts.append(cp)
                    skipped_dup.append((rid, claims))
            else:
                if rid not in processed_ids:
                    updates.append((p, result))
                    processed_ids.add(rid)

    # Preview
    SEP = '=' * 72
    print(SEP)
    print('MIGRATION PREVIEW')
    print(SEP)
    print(f'  Updates:                     {len(updates)}')
    print(f'  Inserts (new products):      {len(inserts)}')
    print(f'  Skipped — ambiguous:         {len(skipped_ambig)}')
    if skipped_dup:
        print(f'  Note: {len(skipped_dup)} DB row(s) claimed by multiple PDF entries '
              '— extra entries will be inserted as new products.')

    print()
    print('── UPDATES ──────────────────────────────────────────────────────────')
    for p, row in updates:
        print(f'\n  SKU {row["sku"]}  |  {row["name_cn_en"]}')
        print(f'    product_type : {row["product_type"]!r:20s} → {p["type"]!r}')
        print(f'    release_date : {row["release_date"]!r:20s} → {p["release"]!r}')
        print(f'    price        : {str(row["price"]):20s} → {p["price"]}')
        print(f'    edition_size : {row["edition_size"]!r:20s} → {p["edition"]!r}')
        has_notes = bool(row['notes'] and row['notes'].strip())
        print(f'    notes        : {"(overwrite existing)" if has_notes else "(set — currently empty)"}')

    print()
    print('── INSERTS (new products) ───────────────────────────────────────────')
    # Peek at next available SKU number without committing
    row = cur.execute(
        "SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1"
    ).fetchone()
    next_num = int(row['sku'][2:]) + 1 if row else 1
    for p in inserts:
        sku_preview = f'SP{next_num:05d}'
        print(f'\n  {sku_preview}  [{p["type"]}]  {p["cn"]} / {p["en"]}')
        print(f'    ip_series    : {p["ip_series"]}')
        print(f'    release_date : {p["release"] or "(unknown)"}')
        print(f'    price        : CAD ${p["price"]}')
        print(f'    edition_size : {p["edition"] or "(unknown)"}')
        next_num += 1

    if skipped_ambig:
        print()
        print('── SKIPPED — AMBIGUOUS ──────────────────────────────────────────────')
        for p, candidates in skipped_ambig:
            skus = ', '.join(r['sku'] for r in candidates)
            print(f'\n  [{p["type"]}] {p["cn"]} / {p["en"]}')
            print(f'    Candidates: {skus}')
            print('    → Skipped. Identify the correct SKU and update manually.')

    print()
    answer = input('Proceed with all updates and inserts above? [y/N]: ').strip().lower()
    if answer != 'y':
        print('Aborted. No changes made.')
        return

    # Execute in a single transaction
    try:
        con.execute('BEGIN')

        for p, row in updates:
            new_blob = make_search_blob(
                row['sku'], row['jizhanming'], row['name_cn_en'],
                p['brand'], p['type'], p['ip_series'],
            )
            cur.execute(
                'UPDATE products '
                'SET product_type=?, release_date=?, price=?, '
                '    edition_size=?, notes=?, ip_series=?, search_blob=? '
                'WHERE id=?',
                (
                    p['type'],
                    p['release'] if p['release'] else row['release_date'],
                    p['price'],
                    p['edition'] if p['edition'] else row['edition_size'],
                    p['notes'],
                    p['ip_series'],
                    new_blob,
                    row['id'],
                ),
            )

        for p in inserts:
            sku = next_sku(cur)
            jzm = f'{p["cn"]} {p["en"]}'.strip()
            blob = make_search_blob(sku, jzm, p['cn'], p['brand'], p['type'], p['ip_series'])
            cur.execute(
                'INSERT INTO products '
                '(sku, name_cn_en, jizhanming, price, ip_series, product_type, '
                ' brand, release_date, edition_size, channel, hidden, '
                ' style_notes, notes, search_blob) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    sku, p['cn'], jzm, p['price'], p['ip_series'], p['type'],
                    p['brand'], p['release'], p['edition'],
                    '', '', '', p['notes'], blob,
                ),
            )

        con.commit()
        print(f'\nDone. {len(updates)} updated, {len(inserts)} inserted.')
        if skipped_ambig:
            print(f'{len(skipped_ambig)} ambiguous entries were skipped.')

    except Exception as exc:
        con.rollback()
        print(f'\nERROR: {exc}')
        print('Transaction rolled back — no changes were made.')
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Mega 大体 product diagnostic and migration.'
    )
    parser.add_argument(
        '--run', action='store_true',
        help='Execute migration (default: diagnostic / read-only mode)',
    )
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f'ERROR: database not found at {DB_PATH}')
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')

    try:
        if args.run:
            run_migrate(con)
        else:
            run_diagnostic(con)
    finally:
        con.close()
