"""Durban supermarket and clothing branches for ``flask seed-stores`` (Step 7).

Only branches that could be confirmed from the retailers' own store pages are listed. The brief asked for
more suburbs than could be verified (see :data:`REQUESTED_BRANCHES` and :func:`unconfirmed_requests`);
those are deliberately NOT invented. Add them to this file, or to a JSON file passed to
``flask seed-stores --file``, once checked on the retailer's store finder:

    Checkers     https://www.checkers.co.za/store-finder
    Pick n Pay   https://www.pnp.co.za/store-locator
    Shoprite     https://www.shoprite.co.za/store-finder
    Woolworths   https://www.woolworths.co.za/storelocator
    SPAR         https://www.spar.co.za/Store-Finder

Coordinates: ``official`` where the retailer publishes them, otherwise ``approximate`` anchors placed at the
shopping centre from general knowledge (typically within a few hundred metres). ``flask seed-stores --geocode``
replaces the approximate ones with an OpenStreetMap lookup of the street address.
"""

from dataclasses import dataclass

CHECKERS_FINDER = "https://www.checkers.co.za/store-finder"
PNP_FINDER = "https://www.pnp.co.za/store-locator"
SHOPRITE_FINDER = "https://www.shoprite.co.za/store-finder"
WOOLWORTHS_FINDER = "https://www.woolworths.co.za/storelocator"
SPAR_FINDER = "https://www.spar.co.za/Store-Finder"


@dataclass(frozen=True)
class StoreSeed:
    slug: str
    name: str
    brand: str
    suburb: str
    address: str
    lat: float
    lng: float
    source: str = "approximate"  # official | geocoded | approximate
    phone: str | None = None
    hours: str | None = None  # OSM opening_hours syntax when known
    link: str | None = None
    covers: tuple[str, ...] = ()  # which requested area(s) this branch satisfies
    kind: str = "grocery"  # grocery | clothing | both (see app.models.store.STORE_TYPES)


DURBAN_STORES: tuple[StoreSeed, ...] = (
    # ---- Checkers ---------------------------------------------------------------------------------
    StoreSeed(
        "checkers-gateway",
        "Checkers Gateway",
        "Checkers",
        "Umhlanga",
        "Shop L002, Gateway Theatre of Shopping, 1 Palm Boulevard, Umhlanga Rocks, 4320",
        -29.7268,
        31.0700,
        phone="031 538 3929",
        hours="Mo-Su 08:00-20:00",
        link=CHECKERS_FINDER,
        covers=("Gateway",),
    ),
    StoreSeed(
        "checkers-pavilion",
        "Checkers Pavilion",
        "Checkers",
        "Westville",
        "Pavilion Shopping Centre, Cnr Harry Gwala Road & Jack Martens Drive, Westville",
        -29.8356,
        30.9293,
        link=CHECKERS_FINDER,
        covers=("Pavilion",),
    ),
    StoreSeed(
        "checkers-westville-mall",
        "Checkers Westville Mall",
        "Checkers",
        "Westville",
        "Westville Mall, Cnr Buckingham Terrace & Menston Road, Westville",
        -29.8330,
        30.9210,
        link=CHECKERS_FINDER,
        covers=("Westville",),
    ),
    StoreSeed(
        "checkers-bluff-towers",
        "Checkers Bluff Towers",
        "Checkers",
        "Bluff",
        "Bluff Towers, 318 Tara Road, Bluff, Durban, 4052",
        -29.9330,
        31.0020,
        link=CHECKERS_FINDER,
        covers=("Bluff",),
    ),
    StoreSeed(
        "checkers-oceans-mall",
        "Checkers Oceans Mall",
        "Checkers",
        "Umhlanga",
        "Oceans Mall, Shop 208, 7 Lagoon Drive, Umhlanga Rocks",
        -29.7268,
        31.0840,
        link=CHECKERS_FINDER,
        covers=("Umhlanga",),
    ),
    # ---- Pick n Pay ---------------------------------------------------------------------------------
    StoreSeed(
        "pnp-chatsworth",
        "Pick n Pay Chatsworth",
        "Pick n Pay",
        "Chatsworth",
        "Cnr Joyhurst & Tranquil Streets, Chatsworth",
        -29.9240,
        30.8890,
        link=PNP_FINDER,
        covers=("Chatsworth",),
    ),
    StoreSeed(
        "pnp-springfield-value-centre",
        "Pick n Pay Springfield Value Centre",
        "Pick n Pay",
        "Springfield",
        "Springfield Value Centre, Shop 23, Springfield, Durban",
        -29.8090,
        30.9880,
        link=PNP_FINDER,
        covers=("Springfield",),
    ),
    StoreSeed(
        "pnp-hyper-springfield",
        "Pick n Pay Hyper Springfield",
        "Pick n Pay",
        "Springfield",
        "Uitsig Road, Springfield, Durban, 4051",
        -29.8100,
        30.9900,
        link=PNP_FINDER,
        covers=("Springfield",),
    ),
    StoreSeed(
        "pnp-umhlanga",
        "Pick n Pay Umhlanga",
        "Pick n Pay",
        "Umhlanga",
        "Shop 16, 14 Chartwell Drive, Umhlanga",
        -29.7265,
        31.0765,
        link=PNP_FINDER,
        covers=("Umhlanga",),
    ),
    StoreSeed(
        "pnp-westville",
        "Pick n Pay Westville",
        "Pick n Pay",
        "Westville",
        "123 Jan Hofmeyer Road, Westville",
        -29.8370,
        30.9230,
        link=PNP_FINDER,
        covers=("Westville",),
    ),
    # ---- Shoprite ---------------------------------------------------------------------------------------
    StoreSeed(
        "shoprite-durban-west-street",
        "Shoprite Durban West Street",
        "Shoprite",
        "Durban Central",
        "424 West Street, Durban Central, Durban",
        -29.8590,
        31.0235,
        link=SHOPRITE_FINDER,
        covers=("Durban Central",),
    ),
    StoreSeed(
        "shoprite-chatsworth",
        "Shoprite Chatsworth",
        "Shoprite",
        "Chatsworth",
        "Unit 5, 13 Joyhurst Street (Cnr Joyhurst & Tranquil Street), Chatsworth",
        -29.9235,
        30.8895,
        link=SHOPRITE_FINDER,
        covers=("Chatsworth",),
    ),
    # ---- Woolworths ---------------------------------------------------------------------------------------
    StoreSeed(
        "woolworths-gateway",
        "Woolworths Gateway",
        "Woolworths",
        "Umhlanga",
        "Gateway Shopping Centre, Cnr Sugar Close & Gateway Boulevard, Umhlanga Rocks, 4320",
        -29.7270,
        31.0690,
        phone="031 566 9300",
        hours="Mo 08:30-18:00; Tu-Th 08:30-19:00; Fr-Sa 08:00-21:00; Su 09:00-18:00",
        link=WOOLWORTHS_FINDER,
        kind="both",
        covers=("Gateway",),
    ),
    StoreSeed(
        "woolworths-granada",
        "Woolworths Granada Centre",
        "Woolworths",
        "Umhlanga",
        "Granada Centre, 16 Chartwell Drive, Umhlanga Rocks",
        -29.7268,
        31.0762,
        link=WOOLWORTHS_FINDER,
        kind="both",
        covers=("Umhlanga",),
    ),
    # Nearest Woolworths to Florida Road (Morningside): a different street, about 1 km away.
    StoreSeed(
        "woolworths-windermere",
        "Woolworths Windermere",
        "Woolworths",
        "Morningside",
        "Windermere Centre, 163 Lilian Ngoyi Road, Morningside, Durban",
        -29.8265,
        31.0140,
        link=WOOLWORTHS_FINDER,
        kind="both",
        covers=("Florida Rd",),
    ),
    # ---- SPAR ------------------------------------------------------------------------------------------------
    StoreSeed(
        "superspar-glenwood",
        "SUPERSPAR Glenwood",
        "SPAR",
        "Glenwood",
        "The Village, Cnr 397 Moore Road & Hunt Road, Glenwood, Durban",
        -29.8543,
        30.9977,
        source="official",
        phone="031 202 2341",
        hours="Mo-Fr 07:00-20:00; Sa,Su,PH 07:00-19:00",
        link="https://www.spar.co.za/home/store-view/superspar-glenwood-kwazulu-natal",
        covers=("Glenwood",),
    ),
    # ---- More branches at the big Durban shopping centres (approximate anchors at the centre) ------------
    StoreSeed(
        "checkers-musgrave",
        "Checkers Musgrave Centre",
        "Checkers",
        "Berea",
        "Musgrave Centre, 115 Musgrave Road, Berea, Durban, 4001",
        -29.8467,
        31.0012,
        link=CHECKERS_FINDER,
    ),
    StoreSeed(
        "pnp-pavilion",
        "Pick n Pay Pavilion",
        "Pick n Pay",
        "Westville",
        "The Pavilion Shopping Centre, Jack Martens Drive, Westville, 3629",
        -29.8497,
        30.9360,
        link=PNP_FINDER,
        covers=("Pavilion",),
    ),
    StoreSeed(
        "pnp-la-lucia-mall",
        "Pick n Pay La Lucia Mall",
        "Pick n Pay",
        "La Lucia",
        "La Lucia Mall, 90 William Campbell Drive, La Lucia, 4051",
        -29.7640,
        31.0600,
        link=PNP_FINDER,
    ),
    StoreSeed(
        "shoprite-umlazi-mega-city",
        "Shoprite Umlazi Mega City",
        "Shoprite",
        "Umlazi",
        "Umlazi Mega City, Cnr Mangosuthu Highway & Griffiths Mxenge Highway, Umlazi, 4031",
        -29.9690,
        30.8840,
        link=SHOPRITE_FINDER,
    ),
    StoreSeed(
        "woolworths-pavilion",
        "Woolworths Pavilion",
        "Woolworths",
        "Westville",
        "The Pavilion Shopping Centre, Jack Martens Drive, Westville, 3629",
        -29.8495,
        30.9365,
        link=WOOLWORTHS_FINDER,
        kind="both",
        covers=("Pavilion",),
    ),
    StoreSeed(
        "woolworths-musgrave",
        "Woolworths Musgrave Centre",
        "Woolworths",
        "Berea",
        "Musgrave Centre, 115 Musgrave Road, Berea, Durban, 4001",
        -29.8469,
        31.0015,
        link=WOOLWORTHS_FINDER,
        kind="both",
    ),
)

# Clothing retailers at the same shopping centres. Positions are approximate anchors at the centre; run
# ``flask seed-stores --geocode`` to refine them.
CLOTHING_STORES: tuple[StoreSeed, ...] = (
    StoreSeed(
        "mrprice-gateway",
        "Mr Price Gateway",
        "Mr Price",
        "Umhlanga",
        "Gateway Theatre of Shopping, 1 Palm Boulevard, Umhlanga Ridge, 4319",
        -29.7272,
        31.0688,
        kind="clothing",
    ),
    StoreSeed(
        "mrprice-pavilion",
        "Mr Price Pavilion",
        "Mr Price",
        "Westville",
        "The Pavilion Shopping Centre, Jack Martens Drive, Westville, 3629",
        -29.8499,
        30.9358,
        kind="clothing",
    ),
    StoreSeed(
        "mrprice-workshop",
        "Mr Price The Workshop",
        "Mr Price",
        "Durban Central",
        "The Workshop, 99 Samora Machel Street, Durban Central, 4001",
        -29.8566,
        31.0275,
        kind="clothing",
    ),
    StoreSeed(
        "pep-west-street",
        "PEP Durban West Street",
        "PEP",
        "Durban Central",
        "Dr Pixley KaSeme (West) Street, Durban Central, 4001",
        -29.8578,
        31.0245,
        kind="clothing",
    ),
    StoreSeed(
        "pep-umlazi-mega-city",
        "PEP Umlazi Mega City",
        "PEP",
        "Umlazi",
        "Umlazi Mega City, Mangosuthu Highway, Umlazi, 4031",
        -29.9692,
        30.8844,
        kind="clothing",
    ),
    StoreSeed(
        "ackermans-pavilion",
        "Ackermans Pavilion",
        "Ackermans",
        "Westville",
        "The Pavilion Shopping Centre, Jack Martens Drive, Westville, 3629",
        -29.8494,
        30.9356,
        kind="clothing",
    ),
    StoreSeed(
        "ackermans-chatsworth",
        "Ackermans Chatsworth Centre",
        "Ackermans",
        "Chatsworth",
        "Chatsworth Centre, Joyhurst Road, Chatsworth, 4092",
        -29.9115,
        30.887,
        kind="clothing",
    ),
    StoreSeed(
        "edgars-gateway",
        "Edgars Gateway",
        "Edgars",
        "Umhlanga",
        "Gateway Theatre of Shopping, 1 Palm Boulevard, Umhlanga Ridge, 4319",
        -29.7266,
        31.0694,
        kind="clothing",
    ),
    StoreSeed(
        "edgars-pavilion",
        "Edgars Pavilion",
        "Edgars",
        "Westville",
        "The Pavilion Shopping Centre, Jack Martens Drive, Westville, 3629",
        -29.8492,
        30.9362,
        kind="clothing",
    ),
    StoreSeed(
        "truworths-gateway",
        "Truworths Gateway",
        "Truworths",
        "Umhlanga",
        "Gateway Theatre of Shopping, 1 Palm Boulevard, Umhlanga Ridge, 4319",
        -29.7269,
        31.0685,
        kind="clothing",
    ),
    StoreSeed(
        "truworths-musgrave",
        "Truworths Musgrave Centre",
        "Truworths",
        "Berea",
        "Musgrave Centre, 115 Musgrave Road, Berea, Durban, 4001",
        -29.8465,
        31.001,
        kind="clothing",
    ),
    StoreSeed(
        "jet-workshop",
        "Jet The Workshop",
        "Jet",
        "Durban Central",
        "The Workshop, 99 Samora Machel Street, Durban Central, 4001",
        -29.8564,
        31.0278,
        kind="clothing",
    ),
    StoreSeed(
        "mrprice-la-lucia",
        "Mr Price La Lucia Mall",
        "Mr Price",
        "La Lucia",
        "La Lucia Mall, 90 William Campbell Drive, La Lucia, 4051",
        -29.7642,
        31.0603,
        kind="clothing",
    ),
    StoreSeed(
        "pep-chatsworth",
        "PEP Chatsworth Centre",
        "PEP",
        "Chatsworth",
        "Chatsworth Centre, Joyhurst Road, Chatsworth, 4092",
        -29.9113,
        30.8873,
        kind="clothing",
    ),
)

# Every branch the seeder knows about: supermarkets first, then the clothing shops.
ALL_STORES: tuple[StoreSeed, ...] = DURBAN_STORES + CLOTHING_STORES

# What the brief asked for, per brand.
REQUESTED_BRANCHES: dict[str, tuple[str, ...]] = {
    "Checkers": (
        "Durban Central",
        "Gateway",
        "Pavilion",
        "Westville",
        "Umhlanga",
        "Chatsworth",
        "Springfield",
        "Bluff",
    ),
    "Pick n Pay": (
        "Durban Central",
        "Gateway",
        "Pavilion",
        "Westville",
        "Umhlanga",
        "Chatsworth",
        "Springfield",
        "Bluff",
    ),
    "Shoprite": (
        "Durban Central",
        "Gateway",
        "Pavilion",
        "Westville",
        "Umhlanga",
        "Chatsworth",
        "Springfield",
        "Bluff",
    ),
    "Woolworths": ("Gateway", "Pavilion", "Umhlanga", "Florida Rd"),
    "SPAR": ("Glenwood", "Morningside", "Musgrave", "Overport"),
}

FINDERS = {
    "Checkers": CHECKERS_FINDER,
    "Pick n Pay": PNP_FINDER,
    "Shoprite": SHOPRITE_FINDER,
    "Woolworths": WOOLWORTHS_FINDER,
    "SPAR": SPAR_FINDER,
}


def unconfirmed_requests(seeds=DURBAN_STORES) -> list[tuple[str, str]]:
    """``(brand, area)`` pairs from the brief that have no confirmed branch in ``seeds``."""
    covered = {(seed.brand, area) for seed in seeds for area in seed.covers}
    return [
        (brand, area) for brand, areas in REQUESTED_BRANCHES.items() for area in areas if (brand, area) not in covered
    ]
