"""Catalogue behind ``MockProvider`` (Step 6): about 200 everyday South African products.

Offline stand-in for the live retail APIs, used in development and in tests. Reference prices are typical
2025/26 shelf prices in ZAR; they are illustrative, not live quotes. Each retailer's price is derived
deterministically from the reference price (see ``services/retail_api.py``), so the same query always gives
the same answer.

Barcodes are synthetic EAN-13 numbers in the ``20``-``29`` range that GS1 reserves for in-store use, so they can
never collide with a real product's barcode. They are stable: the number for a product depends only on its
position in this table, so add new rows only at the very end of the table and never reorder or delete rows.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogueItem:
    barcode: str
    category: str  # Grocery | Toiletries | Clothes  (the budget categories)
    brand: str
    name: str
    price: str  # reference shelf price, ZAR


def ean13_check_digit(first12: str) -> str:
    total = sum(int(char) * (3 if index % 2 else 1) for index, char in enumerate(first12))
    return str((10 - total % 10) % 10)


def synthetic_barcode(position: int) -> str:
    body = f"20{position:010d}"
    return body + ean13_check_digit(body)


# category | brand | name | reference price
_TABLE = """
Grocery|Iwisa|Iwisa Maize Meal 5kg|64.99
Grocery|Iwisa|Iwisa Super Maize Meal 10kg|119.99
Grocery|Ace|Ace Maize Meal 5kg|59.99
Grocery|White Star|White Star Super Maize Meal 5kg|62.99
Grocery|Tastic|Tastic Long Grain White Rice 2kg|44.99
Grocery|Tastic|Tastic Long Grain Parboiled Rice 1kg|26.99
Grocery|Spekko|Spekko Long Grain Parboiled Rice 2kg|41.99
Grocery|Aunt Caroline|Aunt Caroline Basmati Rice 1kg|39.99
Grocery|Sasko|Sasko Cake Flour 2.5kg|42.99
Grocery|Sasko|Sasko Self-Raising Flour 2.5kg|44.99
Grocery|Snowflake|Snowflake Cake Flour 2.5kg|43.99
Grocery|Selati|Selati White Sugar 2kg|44.99
Grocery|Huletts|Huletts Brown Sugar 2kg|46.99
Grocery|Huletts|Huletts White Sugar 1kg|24.99
Grocery|Cerebos|Cerebos Iodated Table Salt 500g|12.99
Grocery|Excella|Excella Sunflower Oil 2L|76.99
Grocery|Sunfoil|Sunfoil Sunflower Oil 750ml|34.99
Grocery|Albany|Albany Superior White Bread 700g|17.99
Grocery|Albany|Albany Best of Both Bread 700g|18.99
Grocery|Sasko|Sasko Brown Bread 700g|17.49
Grocery|Blue Ribbon|Blue Ribbon Brown Bread 700g|17.99
Grocery|Sasko|Sasko White Bread 700g|17.49
Grocery|Sasko|Sasko Hot Dog Rolls 6s|24.99
Grocery|Albany|Albany Hamburger Buns 6s|24.99
Grocery|Clover|Clover Full Cream Fresh Milk 2L|38.99
Grocery|Clover|Clover Low Fat Fresh Milk 2L|38.99
Grocery|Clover|Clover Long Life Full Cream Milk 1L|23.99
Grocery|Parmalat|Parmalat Long Life Full Cream Milk 1L|22.99
Grocery|Danone|Danone Strawberry Yoghurt 1kg|39.99
Grocery|Clover|Clover Mooo Chocolate Milk 500ml|14.99
Grocery|Nulaid|Nulaid Large Eggs 18s|62.99
Grocery|Nulaid|Nulaid Extra Large Eggs 12s|46.99
Grocery|Nulaid|Nulaid Large Eggs 6s|24.99
Grocery|Rama|Rama Original Margarine Tub 500g|36.99
Grocery|Flora|Flora Original Margarine 500g|38.99
Grocery|Clover|Clover Butter 500g|79.99
Grocery|Lancewood|Lancewood Cheddar Cheese 400g|74.99
Grocery|Clover|Clover Cheddar Cheese Slices 200g|44.99
Grocery|Ricoffy|Ricoffy Coffee & Chicory 250g|64.99
Grocery|Nescafé|Nescafé Classic Instant Coffee 200g|109.99
Grocery|Five Roses|Five Roses Tagless Teabags 100s|47.99
Grocery|Joko|Joko Tea Bags 100s|44.99
Grocery|Freshpak|Freshpak Rooibos Teabags 80s|43.99
Grocery|Milo|Milo Chocolate Malt Drink 500g|62.99
Grocery|Ovaltine|Ovaltine Malted Drink 500g|69.99
Grocery|Cremora|Cremora Coffee Creamer 350g|42.99
Grocery|Coca-Cola|Coca-Cola Original 2L|25.99
Grocery|Coca-Cola|Coca-Cola Original 330ml Cans 6-Pack|59.99
Grocery|Fanta|Fanta Orange 2L|25.99
Grocery|Sprite|Sprite 2L|25.99
Grocery|Stoney|Stoney Ginger Beer 2L|25.99
Grocery|Appletiser|Appletiser Sparkling Apple Juice 1.25L|29.99
Grocery|Oros|Oros Orange Squash 2L|59.99
Grocery|Ceres|Ceres Orange Juice 1L|28.99
Grocery|Liqui-Fruit|Liqui-Fruit Orange Juice 1L|27.99
Grocery|Bonaqua|Bonaqua Still Water 500ml|9.99
Grocery|Energade|Energade Naartjie 500ml|15.99
Grocery|Powerade|Powerade Mountain Blast 500ml|16.99
Grocery|Red Bull|Red Bull Energy Drink 250ml|22.99
Grocery|Jungle|Jungle Oats 1kg|38.99
Grocery|Bokomo|Bokomo Weet-Bix 900g|56.99
Grocery|Kellogg's|Kellogg's Corn Flakes 500g|54.99
Grocery|Nestlé|Nestlé Cheerios 500g|69.99
Grocery|Bokomo|Bokomo ProNutro Original 500g|64.99
Grocery|Morvite|Morvite Instant Porridge 1kg|39.99
Grocery|Koo|Koo Baked Beans in Tomato Sauce 410g|14.99
Grocery|Koo|Koo Whole Kernel Corn 410g|15.99
Grocery|Koo|Koo Peach Halves 410g|26.99
Grocery|Koo|Koo Chakalaka Baked Beans 410g|17.99
Grocery|Koo|Koo Chopped Tomatoes 410g|16.99
Grocery|Lucky Star|Lucky Star Pilchards in Tomato Sauce 400g|24.99
Grocery|Lucky Star|Lucky Star Pilchards in Hot Chilli Sauce 400g|24.99
Grocery|Glenryck|Glenryck Tuna Chunks in Brine 170g|24.99
Grocery|All Gold|All Gold Tomato Sauce 700ml|39.99
Grocery|All Gold|All Gold Tomato Paste 115g|13.99
Grocery|Black Cat|Black Cat Crunchy Peanut Butter 400g|34.99
Grocery|Mrs H.S. Ball's|Mrs H.S. Ball's Original Chutney 470g|33.99
Grocery|Marmite|Marmite Yeast Extract 250g|62.99
Grocery|Rhodes|Rhodes Apricot Jam 450g|34.99
Grocery|Bull Brand|Bull Brand Corned Meat 300g|39.99
Grocery|Maggi|Maggi 2-Minute Noodles Chicken 73g|5.99
Grocery|Maggi|Maggi 2-Minute Noodles Bacon 73g|5.99
Grocery|Knorr|Knorr Brown Onion Soup 50g|13.99
Grocery|Royco|Royco Chicken Noodle Soup 50g|12.99
Grocery|Knorrox|Knorrox Beef Stock Cubes 24s|29.99
Grocery|Rajah|Rajah Mild Curry Powder 100g|25.99
Grocery|Robertsons|Robertsons Steak & Chops Spice 200g|38.99
Grocery|Fatti's & Moni's|Fatti's & Moni's Macaroni 500g|19.99
Grocery|Fatti's & Moni's|Fatti's & Moni's Spaghetti 500g|19.99
Grocery|Bakers|Bakers Tennis Biscuits 200g|16.99
Grocery|Bakers|Bakers Eet-Sum-Mor 200g|18.99
Grocery|Bakers|Bakers Chocolate Kit 200g|20.99
Grocery|Cadbury|Cadbury Dairy Milk Slab 80g|19.99
Grocery|Nestlé|Nestlé Lunch Bar 48g|10.99
Grocery|Nestlé|Nestlé Bar One 52g|11.99
Grocery|Simba|Simba Salt & Vinegar Chips 120g|18.99
Grocery|Lay's|Lay's Sour Cream & Onion Chips 120g|18.99
Grocery|Doritos|Doritos Nacho Cheese 145g|22.99
Grocery|Nik Naks|Nik Naks Cheese Curls 135g|17.99
Grocery|Rainbow|Rainbow Frozen Chicken Drumsticks 1kg|59.99
Grocery|Rainbow|Rainbow Chicken Mixed Portions 2kg|84.99
Grocery|Eskort|Eskort Streaky Bacon 200g|44.99
Grocery|Eskort|Eskort Cheese Grillers 500g|54.99
Grocery|Enterprise|Enterprise Chicken Polony 500g|36.99
Grocery|McCain|McCain Mixed Vegetables 1kg|39.99
Grocery|McCain|McCain Steakhouse Chips 1kg|41.99
Grocery|Fresh|Lean Beef Mince 500g Pre-packed|62.99
Grocery|Fresh|Potatoes Washed 2kg Bag|34.99
Grocery|Fresh|Brown Onions 1kg Bag|17.99
Grocery|Fresh|Carrots 1kg Bag|19.99
Grocery|Fresh|Tomatoes Pre-packed 500g|22.99
Grocery|Fresh|Bananas Pre-packed 1kg|24.99
Grocery|Fresh|Apples Royal Gala 1kg Bag|39.99
Grocery|Fresh|Cabbage Whole Each|24.99
Grocery|Fresh|Butternut Pre-packed 1kg|22.99
Grocery|Fresh|Green Peppers Pre-packed 3s|24.99
Grocery|Fresh|Spinach Bunch Each|14.99
Grocery|Jungle|Jungle Muesli Bars Choc Chip 6s|34.99
Grocery|Tastic|Tastic Ready-to-Eat Rice 250g|16.99
Grocery|Sasko|Sasko Bread Flour 2.5kg|46.99
Grocery|Yum Yum|Yum Yum Peanut Butter 400g|29.99
Grocery|Sun-Pat|Sun-Pat Smooth Peanut Butter 400g|39.99
Grocery|Purity|Purity Baby Cereal Oats 200g|27.99
Grocery|Clover|Clover Long Life Low Fat Milk 1L|22.99
Grocery|Cadbury|Cadbury Bournvita 500g|54.99
Grocery|Sasko|Sasko Bran Muffin Mix 500g|32.99
Grocery|Ina Paarman|Ina Paarman Chicken Spice 200g|39.99
Grocery|Nando's|Nando's Peri-Peri Sauce Medium 250ml|39.99
Grocery|Wellington's|Wellington's Sweet Chilli Sauce 500ml|34.99
Grocery|Crosse & Blackwell|Crosse & Blackwell Mayonnaise 750g|59.99
Grocery|Hellmann's|Hellmann's Real Mayonnaise 750g|72.99
Grocery|Denny|Denny Mushrooms Whole Button 400g|24.99
Grocery|Royal|Royal Baking Powder 100g|16.99
Grocery|Moirs|Moirs Custard Powder 250g|22.99
Grocery|Jelly Tots|Jelly Tots Sweets 100g|9.99
Grocery|Beacon|Beacon Chocolate Eclairs 150g|24.99
Grocery|Mentos|Mentos Mint Roll 37.5g|8.99
Toiletries|Ariel|Ariel Auto Washing Powder 2kg|109.99
Toiletries|Omo|Omo Auto Washing Powder 2kg|105.99
Toiletries|Skip|Skip Auto Washing Powder 2kg|99.99
Toiletries|Sunlight|Sunlight Hand Washing Powder 2kg|57.99
Toiletries|Sunlight|Sunlight Dishwashing Liquid Lemon 750ml|34.99
Toiletries|Sunlight|Sunlight Dishwashing Liquid Original 400ml|21.99
Toiletries|Handy Andy|Handy Andy Cream Cleaner Original 750ml|29.99
Toiletries|Domestos|Domestos Thick Bleach Original 750ml|33.99
Toiletries|Jik|Jik Regular Bleach 750ml|24.99
Toiletries|Comfort|Comfort Fabric Softener Pure 800ml|42.99
Toiletries|Sta-Soft|Sta-Soft Fabric Softener Lavender 2L|59.99
Toiletries|Doom|Doom Multi Insect Killer 300ml|55.99
Toiletries|Vim|Vim Scouring Powder 500g|19.99
Toiletries|Glen|Glen 20 Disinfectant Spray 300ml|54.99
Toiletries|Colgate|Colgate Total Toothpaste 100ml|29.99
Toiletries|Colgate|Colgate Maximum Cavity Protection Toothpaste 100ml|27.99
Toiletries|Colgate|Colgate Zig Zag Toothbrush Medium|19.99
Toiletries|Aquafresh|Aquafresh Triple Protection Toothpaste 100ml|26.99
Toiletries|Protex|Protex Antibacterial Soap 150g|13.99
Toiletries|Lux|Lux Soft Touch Soap Bar 175g|12.99
Toiletries|Lifebuoy|Lifebuoy Total Soap Bar 175g|12.99
Toiletries|Dove|Dove Original Beauty Bar 100g|17.99
Toiletries|Vaseline|Vaseline Petroleum Jelly Original 250ml|39.99
Toiletries|Vaseline|Vaseline Intensive Care Body Lotion 400ml|49.99
Toiletries|Nivea|Nivea Roll-On Deodorant Female 50ml|32.99
Toiletries|Rexona|Rexona Roll-On Men Cobalt 50ml|29.99
Toiletries|Axe|Axe Africa Body Spray 150ml|44.99
Toiletries|Sunsilk|Sunsilk Shampoo Black Shine 400ml|42.99
Toiletries|Head & Shoulders|Head & Shoulders Classic Clean Shampoo 400ml|64.99
Toiletries|Clere|Clere Hair Food Petroleum Jelly 250g|36.99
Toiletries|TRESemmé|TRESemmé Keratin Smooth Conditioner 400ml|59.99
Toiletries|Always|Always Ultra Long Pads 10s|31.99
Toiletries|Kotex|Kotex Regular Maxi Pads 10s|27.99
Toiletries|Stayfree|Stayfree Regular Pads 10s|26.99
Toiletries|Carefree|Carefree Panty Liners 40s|24.99
Toiletries|Baby Soft|Baby Soft Toilet Paper 2-Ply 9s|54.99
Toiletries|Twinsaver|Twinsaver Toilet Paper 2-Ply 9s|49.99
Toiletries|Kleenex|Kleenex Facial Tissues 100s|24.99
Toiletries|Dettol|Dettol Antiseptic Liquid 250ml|44.99
Toiletries|Dettol|Dettol Antibacterial Hand Wash 250ml|34.99
Toiletries|Gillette|Gillette Blue II Disposable Razors 5s|39.99
Toiletries|Johnson's|Johnson's Cotton Buds 100s|17.99
Toiletries|Panado|Panado Tablets 24s|29.99
Toiletries|Grand-Pa|Grand-Pa Headache Powders 12s|22.99
Toiletries|Huggies|Huggies Wet Wipes 64s|32.99
Toiletries|Shower to Shower|Shower to Shower Body Powder 200g|32.99
Toiletries|Oral-B|Oral-B Toothbrush Indicator Medium|29.99
Toiletries|Lenor|Lenor Fabric Softener Refill 800ml|44.99
Clothes|Generic|Men's Crew Neck Cotton T-Shirt|79.99
Clothes|Generic|Ladies Cotton T-Shirt|69.99
Clothes|Generic|Ladies Stretch Leggings|99.99
Clothes|Generic|Men's Fleece Track Pants|149.99
Clothes|Generic|Unisex Pullover Hoodie|199.99
Clothes|Generic|Cotton Socks 3-Pack|49.99
Clothes|Generic|Men's Cotton Briefs 3-Pack|89.99
Clothes|Generic|Ladies Cotton Panties 5-Pack|79.99
Clothes|Generic|Rubber Flip-Flops|39.99
Clothes|Generic|Compact Umbrella|59.99
Clothes|Generic|Rain Jacket|179.99
Clothes|Generic|Winter Beanie|49.99
Clothes|Generic|Cotton Face Cloths 3-Pack|29.99
Clothes|Generic|Bath Towel Cotton|99.99
Clothes|Generic|Canvas Backpack 20L|249.99
"""


def _load() -> tuple[CatalogueItem, ...]:
    items = []
    for position, line in enumerate((row for row in _TABLE.strip().splitlines() if row.strip()), start=1):
        category, brand, name, price = (part.strip() for part in line.split("|"))
        items.append(CatalogueItem(synthetic_barcode(position), category, brand, name, price))
    return tuple(items)


CATALOGUE: tuple[CatalogueItem, ...] = _load()
