"""Canonical 87-brand universe definitions, relationships, aliases, and discovery configs."""

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field


class BrandType(str, Enum):
    BRAND = "brand"
    SUB_BRAND = "sub_brand"
    GAMING_LINE = "gaming_line"
    COMPANY_GROUP = "company_group"
    OEM = "oem"


class RelationshipType(str, Enum):
    PARENT = "parent"
    SUBSIDIARY = "subsidiary"
    SUB_BRAND = "sub_brand"
    SIBLING = "sibling"
    HISTORICAL_NAME = "historical_name"
    ALTERNATE_NAME = "alternate_name"
    ECOSYSTEM = "ecosystem"
    MANUFACTURER = "manufacturer"


class DiscoveryStatus(str, Enum):
    SUPPORTED_FULL = "SUPPORTED_FULL"
    SUPPORTED_PARTIAL = "SUPPORTED_PARTIAL"
    METADATA_ONLY = "METADATA_ONLY"
    SOFTWARE_ONLY = "SOFTWARE_ONLY"
    BLOCKED_WAF = "BLOCKED_WAF"
    BLOCKED_REGION = "BLOCKED_REGION"
    NO_OFFICIAL_CATALOG_FOUND = "NO_OFFICIAL_CATALOG_FOUND"
    NO_SOFTWARE_FOUND = "NO_SOFTWARE_FOUND"
    PARSE_FAILED = "PARSE_FAILED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class BrandRelationshipDef(BaseModel):
    target_slug: str
    rel_type: RelationshipType
    confidence: float = 1.0
    provenance: str = ""


class BrandDef(BaseModel):
    slug: str
    canonical_name: str
    batch: str = "A"  # "pilot", "A", "B", "C"
    brand_type: BrandType = BrandType.BRAND
    parent_slug: Optional[str] = None
    website: str = ""
    aliases: list[str] = Field(default_factory=list)
    relationships: list[BrandRelationshipDef] = Field(default_factory=list)
    
    # Discovery configuration
    shopify_url: Optional[str] = None
    catalog_urls: list[str] = Field(default_factory=list)
    sitemap_urls: list[str] = Field(default_factory=list)
    download_urls: list[str] = Field(default_factory=list)
    web_configurator_urls: list[tuple[str, str, str]] = Field(default_factory=list)  # (url, filename, version)
    static_definitions: list[dict[str, Any]] = Field(default_factory=list)
    driver_packages: list[tuple[str, str, str, str]] = Field(default_factory=list)  # (url, filename, version, model)

    def model_post_init(self, __context: Any) -> None:
        # Strip self-referential relationships
        self.relationships = [r for r in self.relationships if r.target_slug != self.slug]



# Complete 87 Canonical Brands Dataset
ALL_CANONICAL_BRANDS: list[BrandDef] = [
    # --- PILOT BRANDS ---
    BrandDef(
        slug="aula",
        canonical_name="AULA",
        batch="pilot",
        website="https://aulagear.com",
        shopify_url="https://aulagear.com/products.json?limit=250",
        download_urls=["https://aulagear.com/blogs/software"],
        relationships=[
            BrandRelationshipDef(target_slug="leobog", rel_type=RelationshipType.ECOSYSTEM, provenance="Dongguan Suoai Electronics parent company"),
        ]
    ),
    BrandDef(
        slug="atk",
        canonical_name="ATK",
        batch="pilot",
        website="https://www.atk.pro",
        web_configurator_urls=[
            ("https://bpcdn.atkgear.com/hub-v3/production/3.2.16/static/index-O22l5tpG.js", "atk_hub_index_v3.2.16.js", "3.2.16"),
            ("https://bpcdn.atkgear.com/hub-v3/production/3.2.16/page-site.min.js", "atk_page_site.min.js", "3.2.16"),
        ],
        relationships=[
            BrandRelationshipDef(target_slug="vgn", rel_type=RelationshipType.ECOSYSTEM, provenance="Shared VGN ecosystem"),
            BrandRelationshipDef(target_slug="vxe", rel_type=RelationshipType.SIBLING, provenance="Sibling brand under VGN ecosystem"),
        ]
    ),
    BrandDef(
        slug="vxe",
        canonical_name="VXE",
        batch="pilot",
        website="https://vxe.com",
        shopify_url="https://vxe.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="vgn", rel_type=RelationshipType.ECOSYSTEM, provenance="Shared VGN ecosystem"),
            BrandRelationshipDef(target_slug="atk", rel_type=RelationshipType.SIBLING, provenance="Sibling brand under VGN ecosystem"),
        ]
    ),
    BrandDef(
        slug="epomaker",
        canonical_name="EPOMAKER",
        batch="pilot",
        website="https://epomaker.com",
        shopify_url="https://epomaker.com/products.json?limit=250",
        web_configurator_urls=[
            ("https://hub.epomaker.com/assets/index-CY06oS50.js", "epomaker_hub_index_v1.0.0.js", "1.0.0"),
        ],
        relationships=[
            BrandRelationshipDef(target_slug="skyloong", rel_type=RelationshipType.ECOSYSTEM, provenance="Historical Epomaker Skyloong partnership"),
        ]
    ),
    BrandDef(
        slug="keychron",
        canonical_name="Keychron",
        batch="pilot",
        website="https://www.keychron.com",
        shopify_url="https://www.keychron.com/products.json?limit=250",
        web_configurator_urls=[
            ("https://launcher.keychron.com/main.b4448c7c630868b5.js", "keychron_launcher_main.js", "1.0.0"),
            ("https://launcher.keychron.com/scripts.e34e0ee36050e207.js", "keychron_launcher_scripts.js", "1.0.0"),
            ("https://launcher.keychron.com/runtime.09f9995a03d86386.js", "keychron_launcher_runtime.js", "1.0.0"),
        ],
        relationships=[
            BrandRelationshipDef(target_slug="lemokey", rel_type=RelationshipType.SUB_BRAND, provenance="Lemokey is Keychron's gaming sub-brand"),
        ]
    ),

    # --- BATCH A (18 Major Global & Enthusiast Brands) ---
    BrandDef(
        slug="logitech_g",
        canonical_name="Logitech G",
        batch="A",
        brand_type=BrandType.GAMING_LINE,
        website="https://www.logitechg.com",
        aliases=["Logitech Gaming", "Logi G"],
        download_urls=["https://support.logi.com/hc/en-us/articles/360025298133-Logitech-G-HUB"],
    ),
    BrandDef(
        slug="razer",
        canonical_name="Razer",
        batch="A",
        website="https://www.razer.com",
        aliases=["Razer Inc."],
        download_urls=["https://www.razer.com/synapse-3"],
    ),
    BrandDef(
        slug="steelseries",
        canonical_name="SteelSeries",
        batch="A",
        website="https://steelseries.com",
        download_urls=["https://steelseries.com/gg/engine"],
    ),
    BrandDef(
        slug="corsair",
        canonical_name="Corsair",
        batch="A",
        website="https://www.corsair.com",
        download_urls=["https://www.corsair.com/downloads"],
        relationships=[
            BrandRelationshipDef(target_slug="drop", rel_type=RelationshipType.SUBSIDIARY, provenance="Drop acquired by Corsair"),
        ]
    ),
    BrandDef(
        slug="asus_rog",
        canonical_name="ASUS ROG",
        batch="A",
        brand_type=BrandType.GAMING_LINE,
        website="https://rog.asus.com",
        aliases=["Republic of Gamers", "ROG", "ASUS TUF Gaming"],
        download_urls=["https://rog.asus.com/armoury-crate/"],
    ),
    BrandDef(
        slug="hyperx",
        canonical_name="HyperX",
        batch="A",
        website="https://hyperx.com",
        aliases=["HyperX Gaming"],
        download_urls=["https://hyperx.com/pages/ngenuity"],
    ),
    BrandDef(
        slug="wooting",
        canonical_name="Wooting",
        batch="A",
        website="https://wooting.io",
        shopify_url="https://wooting.io/products.json?limit=250",
        download_urls=["https://wooting.io/wooting-analog-sdk"],
        web_configurator_urls=[
            ("https://wootility.io/main.js", "wootility_main.js", "4.6.0"),
        ]
    ),
    BrandDef(
        slug="glorious",
        canonical_name="Glorious",
        batch="A",
        website="https://www.gloriousgaming.com",
        aliases=["Glorious PC Gaming Race", "Glorious Gaming"],
        shopify_url="https://www.gloriousgaming.com/products.json?limit=250",
        download_urls=["https://www.gloriousgaming.com/pages/glorious-core"],
    ),
    BrandDef(
        slug="akko",
        canonical_name="Akko",
        batch="A",
        website="https://en.akkogear.com",
        shopify_url="https://en.akkogear.com/products.json?limit=250",
        download_urls=["https://en.akkogear.com/download/"],
        relationships=[
            BrandRelationshipDef(target_slug="monsgeek", rel_type=RelationshipType.SUB_BRAND, provenance="MonsGeek is sub-brand of Akko"),
        ]
    ),
    BrandDef(
        slug="monsgeek",
        canonical_name="MonsGeek",
        batch="A",
        brand_type=BrandType.SUB_BRAND,
        parent_slug="akko",
        website="https://www.monsgeek.com",
        shopify_url="https://www.monsgeek.com/products.json?limit=250",
        download_urls=["https://www.monsgeek.com/download/"],
        relationships=[
            BrandRelationshipDef(target_slug="akko", rel_type=RelationshipType.PARENT, provenance="Akko is parent brand of MonsGeek"),
        ]
    ),
    BrandDef(
        slug="nuphy",
        canonical_name="NuPhy",
        batch="A",
        website="https://nuphy.com",
        shopify_url="https://nuphy.com/products.json?limit=250",
        download_urls=["https://nuphy.com/pages/firmware"],
    ),
    BrandDef(
        slug="royal_kludge",
        canonical_name="Royal Kludge",
        batch="A",
        website="https://rkgamingstore.com",
        aliases=["RK", "RK Royal Kludge"],
        shopify_url="https://rkgamingstore.com/products.json?limit=250",
        download_urls=["https://rkgamingstore.com/pages/software"],
    ),
    BrandDef(
        slug="drunkdeer",
        canonical_name="DrunkDeer",
        batch="A",
        website="https://drunkdeer.com",
        shopify_url="https://drunkdeer.com/products.json?limit=250",
        web_configurator_urls=[
            ("https://app.drunkdeer.com/js/app.js", "drunkdeer_web_app.js", "1.0.0"),
        ]
    ),
    BrandDef(
        slug="pulsar",
        canonical_name="Pulsar Gaming Gears",
        batch="A",
        website="https://www.pulsar.gg",
        aliases=["Pulsar"],
        shopify_url="https://www.pulsar.gg/products.json?limit=250",
        download_urls=["https://www.pulsar.gg/pages/download"],
    ),
    BrandDef(
        slug="lamzu",
        canonical_name="Lamzu",
        batch="A",
        website="https://lamzu.com",
        shopify_url="https://lamzu.com/products.json?limit=250",
        download_urls=["https://lamzu.com/pages/download"],
    ),
    BrandDef(
        slug="cherry",
        canonical_name="CHERRY",
        batch="A",
        website="https://www.cherry-world.com",
        download_urls=["https://www.cherry-world.com/software"],
        relationships=[
            BrandRelationshipDef(target_slug="cherry_xtrfy", rel_type=RelationshipType.ECOSYSTEM, provenance="CHERRY XTRFY gaming line"),
        ]
    ),
    BrandDef(
        slug="zowie",
        canonical_name="ZOWIE",
        batch="A",
        website="https://zowie.benq.com",
        aliases=["BenQ ZOWIE"],
        download_urls=["https://zowie.benq.com/en-us/support.html"],
    ),
    BrandDef(
        slug="cooler_master",
        canonical_name="Cooler Master",
        batch="A",
        website="https://www.coolermaster.com",
        aliases=["CM"],
        download_urls=["https://masterplus.coolermaster.com/"],
    ),

    # --- BATCH B (31 Chinese / Enthusiast Performance Brands) ---
    BrandDef(
        slug="ajazz",
        canonical_name="Ajazz",
        batch="B",
        website="https://ajazzstore.com",
        aliases=["Heijue", "黑爵"],
        shopify_url="https://ajazzstore.com/products.json?limit=250",
        download_urls=["https://ajazzstore.com/pages/software-download"],
    ),
    BrandDef(
        slug="attack_shark",
        canonical_name="Attack Shark",
        batch="B",
        website="https://attackshark.com",
        shopify_url="https://attackshark.com/products.json?limit=250",
        download_urls=["https://attackshark.com/pages/driver-download"],
    ),
    BrandDef(
        slug="darmoshark",
        canonical_name="Darmoshark",
        batch="B",
        website="https://darmoshark.cn",
        aliases=["Motospeed Darmoshark"],
        download_urls=["https://darmoshark.cn/qudongxiazai/"],
    ),
    BrandDef(
        slug="delux",
        canonical_name="Delux",
        batch="B",
        website="https://www.deluxworld.com",
        shopify_url="https://www.deluxworld.com/products.json?limit=250",
        download_urls=["https://www.deluxworld.com/en-driver.html"],
    ),
    BrandDef(
        slug="feker",
        canonical_name="Feker",
        batch="B",
        website="https://fekertech.com",
        shopify_url="https://fekertech.com/products.json?limit=250",
    ),
    BrandDef(
        slug="fl_esports",
        canonical_name="FL·ESPORTS",
        batch="B",
        website="https://flesports.com",
        aliases=["FL Esports", "Flesports"],
        download_urls=["https://flesports.com/pages/download"],
    ),
    BrandDef(
        slug="g_wolves",
        canonical_name="G-Wolves",
        batch="B",
        website="https://shop.g-wolves.com",
        shopify_url="https://shop.g-wolves.com/products.json?limit=250",
    ),
    BrandDef(
        slug="incott",
        canonical_name="Incott",
        batch="B",
        aliases=["HPC", "Incott HPC"],
        website="https://incott.com",
    ),
    BrandDef(
        slug="iqunix",
        canonical_name="IQUNIX",
        batch="B",
        website="https://iqunix.store",
        aliases=["Supercalla"],
        shopify_url="https://iqunix.store/products.json?limit=250",
        download_urls=["https://iqunix.store/pages/software"],
    ),
    BrandDef(
        slug="irok",
        canonical_name="IROK",
        batch="B",
        website="https://irok.cn",
        aliases=["Newmen IROK", "艾岩"],
    ),
    BrandDef(
        slug="kysona",
        canonical_name="Kysona",
        batch="B",
        website="https://kysona.com",
        shopify_url="https://kysona.com/products.json?limit=250",
        download_urls=["https://kysona.com/pages/driver-downloads"],
    ),
    BrandDef(
        slug="leobog",
        canonical_name="Leobog",
        batch="B",
        website="https://leobogtech.com",
        shopify_url="https://leobogtech.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="aula", rel_type=RelationshipType.ECOSYSTEM, provenance="Shared Suoai Electronics parent company"),
        ]
    ),
    BrandDef(
        slug="madlions",
        canonical_name="Madlions",
        batch="B",
        website="https://madlions.cn",
        aliases=["Mad Lions", "狂狮"],
    ),
    BrandDef(
        slug="mchose",
        canonical_name="MCHOSE",
        batch="B",
        website="https://www.mchose.store",
        aliases=["迈从", "Maicong"],
        shopify_url="https://www.mchose.store/products.json?limit=250",
        web_configurator_urls=[
            ("https://hub.mchose.cn/static/js/main.js", "mchose_hub.js", "1.0.0"),
        ]
    ),
    BrandDef(
        slug="melgeek",
        canonical_name="MelGeek",
        batch="B",
        website="https://www.melgeek.com",
        shopify_url="https://www.melgeek.com/products.json?limit=250",
        download_urls=["https://www.melgeek.com/pages/download"],
    ),
    BrandDef(
        slug="ninjutso",
        canonical_name="Ninjutso",
        batch="B",
        website="https://ninjutso.com",
        shopify_url="https://ninjutso.com/products.json?limit=250",
        download_urls=["https://ninjutso.com/pages/download"],
    ),
    BrandDef(
        slug="phylina",
        canonical_name="Phylina",
        batch="B",
        website="https://phylina.com",
    ),
    BrandDef(
        slug="rapoo",
        canonical_name="Rapoo",
        batch="B",
        website="https://rapoo.com",
        download_urls=["https://rapoo.com/download/"],
    ),
    BrandDef(
        slug="rawm",
        canonical_name="Rawm",
        batch="B",
        website="https://rawm.cn",
    ),
    BrandDef(
        slug="scyrox",
        canonical_name="Scyrox",
        batch="B",
        website="https://scyrox.com",
        shopify_url="https://scyrox.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="wlmouse", rel_type=RelationshipType.PARENT, provenance="WLMOUSE ecosystem partner"),
        ]
    ),
    BrandDef(
        slug="sikakeyb",
        canonical_name="Sikakeyb",
        batch="B",
        website="https://sikakeyb.com",
        aliases=["SKK"],
    ),
    BrandDef(
        slug="skyloong",
        canonical_name="Skyloong",
        batch="B",
        website="https://skyloongtech.com",
        aliases=["GK", "Epomaker Skyloong"],
        shopify_url="https://skyloongtech.com/products.json?limit=250",
        download_urls=["https://skyloongtech.com/gk6x-plus-driver/"],
    ),
    BrandDef(
        slug="varmilo",
        canonical_name="Varmilo",
        batch="B",
        website="https://varmilo.com",
        aliases=["阿米洛"],
        shopify_url="https://varmilo.com/products.json?limit=250",
    ),
    BrandDef(
        slug="vgn",
        canonical_name="VGN",
        batch="B",
        website="https://vgn.com",
        relationships=[
            BrandRelationshipDef(target_slug="atk", rel_type=RelationshipType.ECOSYSTEM, provenance="ATK ecosystem brand"),
            BrandRelationshipDef(target_slug="vxe", rel_type=RelationshipType.ECOSYSTEM, provenance="VXE ecosystem brand"),
        ]
    ),
    BrandDef(
        slug="waizowl",
        canonical_name="Waizowl",
        batch="B",
        website="https://waizowl.com",
        shopify_url="https://waizowl.com/products.json?limit=250",
    ),
    BrandDef(
        slug="weikav",
        canonical_name="Weikav",
        batch="B",
        website="https://weikav.com",
    ),
    BrandDef(
        slug="wlmouse",
        canonical_name="WLMOUSE",
        batch="B",
        website="https://www.wlmouse.com",
        shopify_url="https://www.wlmouse.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="scyrox", rel_type=RelationshipType.SUBSIDIARY, provenance="Scyrox sub-brand"),
        ]
    ),
    BrandDef(
        slug="wobkey",
        canonical_name="WOBKEY",
        batch="B",
        website="https://wobkey.com",
        aliases=["WOB"],
        shopify_url="https://wobkey.com/products.json?limit=250",
    ),
    BrandDef(
        slug="womier",
        canonical_name="Womier",
        batch="B",
        website="https://womierkeyboard.com",
        aliases=["XVX", "Womier XVX"],
        shopify_url="https://womierkeyboard.com/products.json?limit=250",
    ),
    BrandDef(
        slug="xinmeng",
        canonical_name="Xinmeng",
        batch="B",
        website="https://xinmeng.cn",
        aliases=["新盟"],
    ),
    BrandDef(
        slug="yunzii",
        canonical_name="Yunzii",
        batch="B",
        website="https://www.yunzii.com",
        shopify_url="https://www.yunzii.com/products.json?limit=250",
        download_urls=["https://www.yunzii.com/pages/software-download"],
    ),
    BrandDef(
        slug="zaopin",
        canonical_name="Zaopin",
        batch="B",
        website="https://zaopin.cn",
    ),

    # --- BATCH C (34 Established / Custom / Regional Ecosystem Brands) ---
    BrandDef(
        slug="a4tech",
        canonical_name="A4Tech",
        batch="C",
        website="https://www.a4tech.com",
        download_urls=["https://www.a4tech.com/download.aspx"],
        relationships=[
            BrandRelationshipDef(target_slug="bloody", rel_type=RelationshipType.SUB_BRAND, provenance="Bloody is A4Tech's gaming sub-brand"),
        ]
    ),
    BrandDef(
        slug="bloody",
        canonical_name="Bloody",
        batch="C",
        brand_type=BrandType.SUB_BRAND,
        parent_slug="a4tech",
        website="https://www.bloody.com",
        aliases=["Bloody Gaming", "A4Tech Bloody"],
        download_urls=["https://www.bloody.com/en/download.php"],
        relationships=[
            BrandRelationshipDef(target_slug="a4tech", rel_type=RelationshipType.PARENT, provenance="A4Tech is parent company of Bloody"),
        ]
    ),
    BrandDef(
        slug="alienware",
        canonical_name="Alienware",
        batch="C",
        brand_type=BrandType.GAMING_LINE,
        website="https://www.dell.com/alienware",
        aliases=["Dell Alienware"],
        download_urls=["https://www.dell.com/support/home/en-us/drivers/driversdetails?driverid=w6kvy"],
    ),
    BrandDef(
        slug="cougar_gaming",
        canonical_name="Cougar Gaming",
        batch="C",
        website="https://cougargaming.com",
        aliases=["Cougar"],
        download_urls=["https://cougargaming.com/downloads/"],
    ),
    BrandDef(
        slug="drop",
        canonical_name="Drop",
        batch="C",
        website="https://drop.com",
        aliases=["Massdrop"],
        relationships=[
            BrandRelationshipDef(target_slug="corsair", rel_type=RelationshipType.PARENT, provenance="Corsair is parent company of Drop"),
        ]
    ),
    BrandDef(
        slug="ducky",
        canonical_name="Ducky",
        batch="C",
        website="https://www.duckychannel.com.tw",
        aliases=["DuckyChannel"],
        download_urls=["https://www.duckychannel.com.tw/en/Support/Download"],
    ),
    BrandDef(
        slug="endgame_gear",
        canonical_name="Endgame Gear",
        batch="C",
        website="https://www.endgamegear.com",
        aliases=["EGG"],
        shopify_url="https://www.endgamegear.com/products.json?limit=250",
        download_urls=["https://www.endgamegear.com/downloads"],
    ),
    BrandDef(
        slug="filco",
        canonical_name="Filco",
        batch="C",
        website="https://www.diatec.co.jp",
        aliases=["Diatec Filco"],
    ),
    BrandDef(
        slug="finalmouse",
        canonical_name="Finalmouse",
        batch="C",
        website="https://finalmouse.com",
    ),
    BrandDef(
        slug="fnatic_gear",
        canonical_name="Fnatic Gear",
        batch="C",
        website="https://fnatic.com/gear",
        aliases=["Fnatic"],
        shopify_url="https://fnatic.com/products.json?limit=250",
        download_urls=["https://fnatic.com/op"],
    ),
    BrandDef(
        slug="hhkb",
        canonical_name="HHKB",
        batch="C",
        website="https://happyhackingkb.com",
        aliases=["Happy Hacking Keyboard", "PFU HHKB"],
        download_urls=["https://happyhackingkb.com/download/"],
    ),
    BrandDef(
        slug="leopold",
        canonical_name="Leopold",
        batch="C",
        website="https://leopold.co.kr",
    ),
    BrandDef(
        slug="mode_designs",
        canonical_name="Mode Designs",
        batch="C",
        website="https://modedesigns.com",
        shopify_url="https://modedesigns.com/products.json?limit=250",
    ),
    BrandDef(
        slug="msi",
        canonical_name="MSI",
        batch="C",
        website="https://www.msi.com",
        aliases=["Micro-Star International"],
        download_urls=["https://www.msi.com/Landing/dragon-center-download"],
    ),
    BrandDef(
        slug="nzxt",
        canonical_name="NZXT",
        batch="C",
        website="https://nzxt.com",
        shopify_url="https://nzxt.com/products.json?limit=250",
        download_urls=["https://nzxt.com/software/cam"],
    ),
    BrandDef(
        slug="realforce",
        canonical_name="Realforce",
        batch="C",
        website="https://www.realforce.co.jp",
        aliases=["Topre Realforce"],
        download_urls=["https://www.realforce.co.jp/support/download/"],
    ),
    BrandDef(
        slug="turtle_beach",
        canonical_name="Turtle Beach",
        batch="C",
        website="https://www.turtlebeach.com",
        aliases=["ROCCAT", "Turtle Beach ROCCAT"],
        download_urls=["https://support.turtlebeach.com/s/downloads"],
    ),
    BrandDef(
        slug="vaxee",
        canonical_name="VAXEE",
        batch="C",
        website="https://www.vaxee.co",
    ),
    BrandDef(
        slug="ardor_gaming",
        canonical_name="ARDOR GAMING",
        batch="C",
        website="https://ardor-gaming.com",
        aliases=["ZET GAMING"],
        download_urls=["https://ardor-gaming.com/drivers/"],
    ),
    BrandDef(
        slug="chilkey",
        canonical_name="Chilkey",
        batch="C",
        website="https://chilkey.com",
        shopify_url="https://chilkey.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="wuque_studio", rel_type=RelationshipType.ECOSYSTEM, provenance="Wuque Studio ecosystem brand"),
        ]
    ),
    BrandDef(
        slug="cidoo",
        canonical_name="Cidoo",
        batch="C",
        website="https://cidootech.com",
        shopify_url="https://cidootech.com/products.json?limit=250",
    ),
    BrandDef(
        slug="dareu",
        canonical_name="Dareu",
        batch="C",
        website="https://dareu.com",
        aliases=["达尔优"],
        download_urls=["https://dareu.com/pages/driver"],
    ),
    BrandDef(
        slug="dark_project",
        canonical_name="Dark Project",
        batch="C",
        website="https://darkproject.eu",
        aliases=["DP"],
        download_urls=["https://darkproject.eu/software/"],
    ),
    BrandDef(
        slug="io_by_red_square",
        canonical_name="IO by Red Square",
        batch="C",
        brand_type=BrandType.SUB_BRAND,
        parent_slug="red_square",
        website="https://io-gaming.ru",
        aliases=["IO", "IO Gaming"],
        download_urls=["https://io-gaming.ru/support/"],
        relationships=[
            BrandRelationshipDef(target_slug="red_square", rel_type=RelationshipType.PARENT, provenance="Red Square is parent brand of IO"),
        ]
    ),
    BrandDef(
        slug="kbdfans",
        canonical_name="KBDfans",
        batch="C",
        website="https://kbdfans.com",
        shopify_url="https://kbdfans.com/products.json?limit=250",
    ),
    BrandDef(
        slug="lemokey",
        canonical_name="Lemokey",
        batch="C",
        brand_type=BrandType.SUB_BRAND,
        parent_slug="keychron",
        website="https://www.lemokey.com",
        shopify_url="https://www.lemokey.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="keychron", rel_type=RelationshipType.PARENT, provenance="Keychron is parent brand of Lemokey"),
        ]
    ),
    BrandDef(
        slug="machenike",
        canonical_name="Machenike",
        batch="C",
        website="https://global.machenike.com",
        shopify_url="https://global.machenike.com/products.json?limit=250",
        download_urls=["https://global.machenike.com/pages/drivers"],
    ),
    BrandDef(
        slug="meletrix",
        canonical_name="Meletrix",
        batch="C",
        website="https://meletrix.com",
        shopify_url="https://meletrix.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="wuque_studio", rel_type=RelationshipType.ECOSYSTEM, provenance="Wuque Studio ecosystem brand"),
        ]
    ),
    BrandDef(
        slug="qwertykeys",
        canonical_name="Qwertykeys",
        batch="C",
        website="https://www.qwertykeys.com",
        aliases=["Owlab", "Neo", "NeoStudio"],
        shopify_url="https://www.qwertykeys.com/products.json?limit=250",
    ),
    BrandDef(
        slug="red_square",
        canonical_name="Red Square",
        batch="C",
        website="https://red-square.org",
        download_urls=["https://red-square.org/support/"],
        relationships=[
            BrandRelationshipDef(target_slug="io_by_red_square", rel_type=RelationshipType.SUB_BRAND, provenance="IO is sub-brand of Red Square"),
        ]
    ),
    BrandDef(
        slug="thunderobot",
        canonical_name="Thunderobot",
        batch="C",
        website="https://thunderobot.com",
        download_urls=["https://thunderobot.com/driver"],
    ),
    BrandDef(
        slug="wuque_studio",
        canonical_name="Wuque Studio",
        batch="C",
        website="https://wuquestudio.com",
        shopify_url="https://wuquestudio.com/products.json?limit=250",
        relationships=[
            BrandRelationshipDef(target_slug="meletrix", rel_type=RelationshipType.ECOSYSTEM, provenance="Meletrix is brand under Wuque Studio"),
            BrandRelationshipDef(target_slug="chilkey", rel_type=RelationshipType.ECOSYSTEM, provenance="Chilkey is brand under Wuque Studio"),
        ]
    ),
    # --- 13 NEW ENTHUSIAST & PERFORMANCE BRANDS ---
    BrandDef(
        slug="chosfox",
        canonical_name="Chosfox",
        batch="C",
        website="https://chosfox.com",
        shopify_url="https://chosfox.com/products.json?limit=250",
    ),
    BrandDef(
        slug="matrix_lab",
        canonical_name="Matrix Lab",
        batch="C",
        website="https://matrixlab.store",
        aliases=["Matrix"],
    ),
    BrandDef(
        slug="lin_works",
        canonical_name="Lin Works",
        batch="C",
        website="https://linworks.net",
        aliases=["Lin", "Lyn"],
    ),
    BrandDef(
        slug="tgr",
        canonical_name="TGR",
        batch="C",
        website="https://tgrkeyboards.com",
        aliases=["TGR Keyboards", "Yuktsi TGR"],
    ),
    BrandDef(
        slug="keycult",
        canonical_name="Keycult",
        batch="C",
        website="https://keycult.com",
        shopify_url="https://keycult.com/products.json?limit=250",
    ),
    BrandDef(
        slug="kzzi",
        canonical_name="Kzzi",
        batch="B",
        website="https://kzzitech.com",
        aliases=["KzziTech"],
        shopify_url="https://kzzitech.com/products.json?limit=250",
        download_urls=["https://kzzitech.com/pages/download"],
    ),
    BrandDef(
        slug="gamakay",
        canonical_name="Gamakay",
        batch="B",
        website="https://gamakay.com",
        shopify_url="https://gamakay.com/products.json?limit=250",
        download_urls=["https://gamakay.com/pages/downloads"],
    ),
    BrandDef(
        slug="x_bows",
        canonical_name="X-Bows",
        batch="C",
        website="https://x-bows.com",
        aliases=["XBows"],
        shopify_url="https://x-bows.com/products.json?limit=250",
        download_urls=["https://x-bows.com/pages/download"],
    ),
    BrandDef(
        slug="tecware",
        canonical_name="Tecware",
        batch="C",
        website="https://www.tecware.co",
        download_urls=["https://www.tecware.co/software"],
    ),
    BrandDef(
        slug="ymdk",
        canonical_name="YMDK",
        batch="C",
        website="https://ymdkey.com",
        aliases=["YMDKey"],
        shopify_url="https://ymdkey.com/products.json?limit=250",
    ),
    BrandDef(
        slug="e_yooso",
        canonical_name="E-Yooso",
        batch="B",
        website="https://e-yooso.net",
        aliases=["EYOOSO", "E-Element"],
        shopify_url="https://e-yooso.net/products.json?limit=250",
        download_urls=["https://e-yooso.net/pages/driver-download"],
    ),
    BrandDef(
        slug="kemove",
        canonical_name="Kemove",
        batch="B",
        website="https://kemove.com",
        shopify_url="https://kemove.com/products.json?limit=250",
        download_urls=["https://kemove.com/pages/download"],
    ),
    BrandDef(
        slug="fantech",
        canonical_name="Fantech",
        batch="C",
        website="https://fantechworld.com",
        aliases=["Fantech Gaming", "Fantech World"],
        shopify_url="https://fantechworld.com/products.json?limit=250",
        download_urls=["https://fantechworld.com/download-and-support/"],
    ),
]


def get_all_brands() -> list[BrandDef]:
    return ALL_CANONICAL_BRANDS


def get_brand_by_slug(slug: str) -> Optional[BrandDef]:
    slug_clean = slug.strip().lower()
    for b in ALL_CANONICAL_BRANDS:
        if b.slug == slug_clean or b.canonical_name.lower() == slug_clean or slug_clean in [a.lower() for a in b.aliases]:
            return b
    return None


def get_brands_by_batch(batch: str) -> list[BrandDef]:
    b_upper = batch.strip().upper()
    if b_upper == "ALL":
        return ALL_CANONICAL_BRANDS
    if b_upper == "PILOT":
        return [b for b in ALL_CANONICAL_BRANDS if b.batch == "pilot"]
    return [b for b in ALL_CANONICAL_BRANDS if b.batch.upper() == b_upper]
