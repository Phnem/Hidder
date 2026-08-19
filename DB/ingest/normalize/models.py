"""Product name normalization, accurate category classification, and category evidence resolution."""

import re
from typing import NamedTuple

# Stopwords to clean from raw titles for canonical naming
STOPWORDS = [
    r'\bmechanical\b', r'\bgaming\b', r'\bkeyboard\b', r'\bmouse\b', r'\bmice\b',
    r'\bwireless\b', r'\bwired\b', r'\btri-mode\b', r'\bbluetooth\b', r'\b2\.4g\b',
    r'\brgb\b', r'\bcustom\b', r'\bdriver\b', r'\bsoftware\b', r'\bconfigurator\b',
    r'\bfirmware\b', r'\bupdate\b', r'\bmanual\b', r'\bedition\b', r'\bofficial\b',
    r'\bdownload\b', r'\bcollection\b', r'\blayout\b', r'\bansi\b', r'\biso\b',
    r'\bgasket\b', r'\bstructure\b', r'\bmount\b', r'\bversion\b'
]

RE_STOPWORDS = re.compile('|'.join(STOPWORDS), re.IGNORECASE)
RE_WHITESPACE = re.compile(r'\s+')
RE_CLEAN_PUNCT = re.compile(r'[^\w\d\s\-\+\.]')

# Negative Patterns in Titles (checked BEFORE device & model heuristics)
RE_NON_PERIPHERAL = re.compile(
    r'\b(battery charger|car charger|tricycle|electric vehicle|3d print|3d printer|bathroom|sanitary|'
    r'hardware fitting|sink|faucet|power tool|lawn mower|clothing|t-shirt|hoodie|shoes|socks|mug|bottle|drink)\b',
    re.IGNORECASE
)
RE_BUNDLE_TITLE = re.compile(r'\b(bundle|combo|set with mouse|keyboard and mouse|mouse and keyboard|\+\s*carbonx|\+\s*mouse|combo\b)', re.IGNORECASE)
RE_PREORDER_TITLE = re.compile(r'\b(reservation|reservation card|pre-order card|preorder card|deposit)\b', re.IGNORECASE)
RE_GIFT_CARD_TITLE = re.compile(r'\b(gift card|voucher)\b', re.IGNORECASE)
RE_PROJECT_DOCS_TITLE = re.compile(r'\b(open source project|design project|source project|firmware source|layout project)\b', re.IGNORECASE)
RE_DISPLAY_PART_TITLE = re.compile(r'\b(smart mini display|mini display|display module|lcd screen|screen module|replacement|spare part|case module|pcb|foam kit|weight bar|badge|knob module)\b', re.IGNORECASE)

# Specific Non-Device Accessories Keywords (checked on title BEFORE generic device keywords)
KEYCAP_TITLE_PATTERNS = [
    r'\bkeycap\s+set\b', r'\bkeycaps\s+set\b', r'\bkeyset\b', r'\bartisan\s+keycap\b', r'\bartisan\b',
    r'\bkeycaps\b', r'\bkeycap\b', r'\bkey cap\b', r'\bkey caps\b', r'\bкейкап\b', r'\bкейкапы\b'
]
SWITCH_TITLE_PATTERNS = [
    r'\bswitch\s+set\b', r'\bswitch\s+pack\b', r'\bswitches\s+pack\b', r'\bswitches\s+set\b',
    r'\blinear\s+switches?\b', r'\btactile\s+switches?\b', r'\bclicky\s+switches?\b', r'\bsilent\s+switches?\b',
    r'\bmagnetic\s+switches?\b', r'\boptical\s+switches?\b', r'\bswitches?\b', r'\bсвитчи\b', r'\bпереключатели\b'
]
CABLE_TITLE_PATTERNS = [
    r'\baviator\s+cable\b', r'\bcoiled\s+cable\b', r'\btype-c\s+cable\b', r'\busb\s+cable\b',
    r'\bjst\s+cable\b', r'\bglowing\s+cable\b', r'\bcables?\b', r'\bкабель\b', r'\bпровод\b'
]
DONGLE_TITLE_PATTERNS = [
    r'\bdongles?\b', r'\b4k\s+receiver\b', r'\b8k\s+receiver\b', r'\bwireless\s+receiver\b',
    r'\breceivers?\b', r'\bадаптер\b', r'\bдонгл\b'
]
PAD_TITLE_PATTERNS = [
    r'\bmousepads?\b', r'\bdeskmats?\b', r'\bmouse\s+pads?\b', r'\bdesk\s+mats?\b', r'\bковрик\b'
]
ACCESSORY_TITLE_PATTERNS = [
    # Arms, Stands & Mounts
    r'\bboom\s+arms?\b', r'\bmic\s+arms?\b', r'\bmicrophone\s+arms?\b', r'\bmic\s+stands?\b', r'\bmicrophone\s+stands?\b',
    r'\bheadphone\s+stands?\b', r'\bheadset\s+stands?\b', r'\bkeyboard\s+stands?\b', r'\bdisplay\s+stands?\b',
    r'\bheadphone\s+(?:holder|hanger|hook)s?\b', r'\bheadset\s+(?:holder|hanger|hook)s?\b',
    r'\b(?:black\s+walnut|walnut|wooden|wood|acrylic|metal|aluminum)?\s*stands?\b',
    r'\bshock\s+mount\b', r'\bpop\s+filter\b',

    # Wrist rests & Palm rests
    r'\b(?:solid\s+wood|beech\s+wood|walnut|wooden|wood|resin|leather|silicone|foam|memory\s+foam|tkl|full\s+size|\d+)?\s*wrists?\s*(?:rests?|pads?|supports?)?\b',
    r'\bwrists?\s+rests?\b', r'\bwrist\s+supports?\b', r'\bpalm\s+rests?\b', r'\bhand\s+rests?\b', r'\barm\s+rests?\b',
    r'\bwrists?\b',

    # Covers & Dust Covers
    r'\b\d+-key\s+covers?\b', r'\bkeyboard\s+covers?\b', r'\bkey\s+covers?\b', r'\bdust\s+covers?\b',
    r'\bacrylic\s+covers?\b', r'\bprotective\s+covers?\b', r'\bcovers?\b',

    # Cases, Sleeves & Storage
    r'\bcarrying\s+cases?\b', r'\bcarry\s+cases?\b', r'\btravel\s+cases?\b', r'\bstorage\s+cases?\b',
    r'\bprotective\s+cases?\b', r'\bhard\s+cases?\b', r'\bsleeves?\b', r'\bpouch(?:es)?\b',
    r'\btop\s+cases?\b', r'\bbottom\s+cases?\b',
    r'\b(?:tofu\d*|blade\d*|aluminum|alu|wood|wooden|acrylic|pc|brass|resin|replacement)\s+cases?\b',

    # Skates, Feet & Glides
    r'\b(?:mouse\s+|glass\s+|ptfe\s+|ice\s+|dots?\s+|speed\s+|control\s+|replacement\s+|custom\s+)?skate[sz]\b',
    r'\bstrikeskate[sz]?\b', r'\bsuperglide[sz]?\b', r'\bglide\s+feet\b', r'\bmouse\s+feet\b',
    r'\bptfe\s+feet\b', r'\brubber\s+feet\b', r'\bcase\s+feet\b', r'\bbumpons?\b',
    r'\banti-slip\s+pads?\b', r'\bprotective\s+pads?\b',

    # Grips & Grip Tape
    r'\b(?:mouse\s+|pro\s+|anti-slip\s+|custom\s+|replacement\s+)?grip\s+tapes?\b',
    r'\bmouse\s+grips?\b', r'\bpro\s+grips?\b', r'\bgrip\s+stickers?\b', r'\bgrips?\b',

    # Shells & Housings
    r'\b(?:replacement\s+|mouse\s+|top\s+|bottom\s+|front\s+|back\s+|outer\s+|custom\s+)?shells?\b',
    r'\b(?:replacement\s+|mouse\s+)?housings?\b',

    # Pullers, Openers & Tools
    r'\bswitch\s+pullers?\b', r'\bkeycap\s+pullers?\b', r'\bkey\s+pullers?\b', r'\bpullers?\b',
    r'\bswitch\s+removers?\b', r'\bkeycap\s+removers?\b', r'\bremovers?\b',
    r'\bswitch\s+openers?\b', r'\bopeners?\b', r'\bstem\s+holders?\b',
    r'\blube\s+station\b', r'\blube\s+brush\b', r'\blubricants?\b', r'\blubes?\b', r'\bcleaning\s+kits?\b',
    r'\bswitch\s+films?\b', r'\bswitch\s+pads?\b',

    # PCBs & Plates
    r'\b(?:hotswap|hot-swap|solder|soldered|replacement|rgb|bluetooth|wireless)?\s*pcbs?\b',
    r'\bdaughterboards?\b',
    r'\b(?:switch|brass|fr4|alu|aluminum|pc|polycarbonate|pom|copper|carbon\s+fiber|steel|back)\s+plates?\b',
    r'\bplates?\s+for\b', r'\bbackplates?\b',
    r'\bweight\s+bars?\b', r'\bbottom\s+weights?\b', r'\bbrass\s+weights?\b', r'\bknobs?\b', r'\brotary\s+knobs?\b', r'\bbadges?\b',

    # Stabilizers
    r'\bscrew-in\s+stabilizers?\b', r'\bplate\s+mount\s+stabilizers?\b', r'\bpcb\s+mount\s+stabilizers?\b',
    r'\bstabilizers?\b', r'\bstabs?\b',

    # Foams, Gaskets & Dampeners
    r'\bfoam\s+kits?\b', r'\bporon\s+foam\b', r'\bpe\s+foam\b', r'\bcase\s+foam\b', r'\bplate\s+foam\b',
    r'\bdampeners?\b', r'\bdampening\b',
    r'\bgasket\s+strips?\b', r'\bgasket\s+socks?\b', r'\breplacement\s+gaskets?\b',
    r'\bhardware\s+kits?\b', r'\bscrews?\b', r'\bfasteners?\b',

    # Multilingual terms
    r'\bсмазка\b', r'\bпуллер\b', r'\bподставка\b'
]

# Contextual Complete Keyboard Kits Filter
RE_KEYBOARD_KITS = re.compile(
    r'\b(keyboard\s+kit|barebone\s+kit|barebones\s+kit|diy\s+kit|diy\s+keyboard|keyboard\s+diy|pre-built\s+keyboard|assembled\s+keyboard|custom\s+keyboard\s+kit)\b',
    re.IGNORECASE
)
RE_COMPONENT_KITS = re.compile(
    r'\b(foam\s+kit|lube\s+kit|lubing\s+kit|cleaning\s+kit|mod\s+kit|stabilizer\s+kit|stab\s+kit|gasket\s+kit|switch\s+pack|switch\s+set|keycap\s+set)\b',
    re.IGNORECASE
)


# Explicit Hardware Model Families (Major keyboard & mouse lineups)
RE_KEYBOARD_MODELS = re.compile(
    r'\b(F\d{2,3}|HE\d{2,3}|TH\d{2,3}|RT\d{2,3}|QK\d{2,3}|GK\d{2,3}|SK\d{2,3}|Q\d{1,2}|V\d{1,2}|K\d{1,2}|'
    r'GALAXY\s*\d{2,3}|CIDOO\s*V\d{2,3}|SHADOW-X|DYNATAB|EK\d{2,3}|HERO\s*\d{2,3}|AULA\s*F\d{2,3}|'
    r'LEMOKEY\s*[LX]\d|B[1-9]\s*PRO|BRIDGE75|CRUSH80|ND75|RAINY75|KBD\d{2,3}|TIGER\d{2,3}|FREEBIRD\d{2,3}|ZOOM\d{2,3}|NEO\d{2,3})\b',
    re.IGNORECASE
)
RE_MOUSE_MODELS = re.compile(
    r'\b(BLAZING\s*SKY\s*F\d|DRAGONFLY\s*[RF]\d|MAD\s*R|SC\d{3}|M\d{1,2}|G\d{1,2}|NEX\s*(PRO|LITE)|X1|RS7|ATK\s*F1|ATK\s*X1)\b',
    re.IGNORECASE
)

# Device General Keywords
RE_MOUSE = re.compile(r'\b(mouse|mice|мышь|мышка|paw3395|paw3950|paw3370|paw3311|paw3335|wireless mouse|gaming mouse|optical mouse|polling rate)\b', re.IGNORECASE)
RE_KEYBOARD = re.compile(r'\b(keyboard|клавиатура|numpad|keypad|keeb|mechanical keyboard|gaming keyboard|custom keyboard|75%|65%|80%|96%|84|87|99|104|108)\b', re.IGNORECASE)
RE_AUDIO = re.compile(r'\b(headset|headphone|earphone|earphones|iem|earbuds|наушники|гарнитура)\b', re.IGNORECASE)
RE_MIC = re.compile(r'\b(microphone|mic|микрофон)\b', re.IGNORECASE)

RE_KEYCAP_TITLE = re.compile('|'.join(KEYCAP_TITLE_PATTERNS), re.IGNORECASE)
RE_SWITCH_TITLE = re.compile('|'.join(SWITCH_TITLE_PATTERNS), re.IGNORECASE)
RE_CABLE_TITLE = re.compile('|'.join(CABLE_TITLE_PATTERNS), re.IGNORECASE)
RE_DONGLE_TITLE = re.compile('|'.join(DONGLE_TITLE_PATTERNS), re.IGNORECASE)
RE_PAD_TITLE = re.compile('|'.join(PAD_TITLE_PATTERNS), re.IGNORECASE)
RE_ACCESSORY_TITLE = re.compile('|'.join(ACCESSORY_TITLE_PATTERNS), re.IGNORECASE)

RE_SOFTWARE_FILENAME = re.compile(
    r'(\.(?:zip|exe|msi|dmg|pkg|7z|rar|bin|hex|iso|tar\.gz|tgz)|\b[0-9a-f]{32,64}\b)',
    re.IGNORECASE
)


def is_software_filename(name: str) -> bool:
    """Return True if name represents a software/download filename rather than a hardware product model."""
    name_clean = name.strip()
    return bool(RE_SOFTWARE_FILENAME.search(name_clean))


class CategoryEvaluation(NamedTuple):
    category: str
    confidence: float
    reason: str


def evaluate_category(
    name: str,
    description: str = "",
    product_url: str = "",
    tags: list[str] | None = None,
    product_type: str | None = None
) -> CategoryEvaluation:
    """
    Multi-level category evaluation with confidence resolution.
    Precedence:
    1. Negative non-peripheral semantics (0.95)
    2. Negative auxiliary semantics: bundles, preorder cards, gift cards, project pages, display parts (0.85-0.95)
    3. Standalone accessories titles (boom arm, skates, grips, keycaps, switches, cables, pads, dongles, wrist rests, cases) (0.85)
    4. Explicit audio / mic in title (0.85)
    5. Explicit vendor product type / collection (0.85)
    6. Known model family matching (0.75)
    7. URL semantics (0.70)
    8. Device patterns in name (0.65)
    9. Fallback specs/tags in full_text (0.40)
    10. Fallback unclassified (0.10)
    """
    name_clean = name.strip()
    tags_text = " ".join(tags).lower() if tags else ""
    full_text = f"{name_clean} {tags_text} {description} {product_url}".lower()

    # 1. Negative Non-Peripheral Goods (Strict Domain / Product Filter)
    if RE_NON_PERIPHERAL.search(name_clean):
        return CategoryEvaluation("other", 0.95, "Non-peripheral product (machinery / vehicle / household)")

    # 2. Negative Auxiliary Semantics
    if RE_GIFT_CARD_TITLE.search(name_clean):
        return CategoryEvaluation("other", 0.95, "Gift card / voucher")
    if RE_PROJECT_DOCS_TITLE.search(name_clean):
        return CategoryEvaluation("other", 0.95, "Project / documentation page")
    if RE_PREORDER_TITLE.search(name_clean):
        return CategoryEvaluation("other", 0.90, "Preorder / reservation item")
    if RE_BUNDLE_TITLE.search(name_clean):
        return CategoryEvaluation("bundle", 0.90, "Multi-device bundle / combo SKU")
    if RE_DISPLAY_PART_TITLE.search(name_clean):
        return CategoryEvaluation("accessory", 0.85, "Display module / replacement part")

    # 3. Standalone accessories & components in title (Checked BEFORE generic device keywords/models)
    if RE_KEYCAP_TITLE.search(name_clean):
        return CategoryEvaluation("keycap", 0.85, "Explicit keycap in title")
    if RE_SWITCH_TITLE.search(name_clean) and not ("keyboard" in name_clean.lower() and not any(k in name_clean.lower() for k in ["switch set", "switch pack", "switches pack", "switches set", "pcs"])):
        return CategoryEvaluation("switch", 0.85, "Explicit switch in title")
    if RE_CABLE_TITLE.search(name_clean):
        return CategoryEvaluation("cable", 0.85, "Explicit cable in title")
    if RE_ACCESSORY_TITLE.search(name_clean):
        # Contextual check: Do not classify genuine complete DIY/barebone keyboard kits as accessories
        if RE_KEYBOARD_KITS.search(name_clean) and not RE_COMPONENT_KITS.search(name_clean):
            return CategoryEvaluation("keyboard", 0.85, "Complete DIY / Barebone Keyboard Kit")
        return CategoryEvaluation("accessory", 0.85, "Explicit accessory in title (arm / stand / skates / grip / case / plate / rest)")
    if RE_PAD_TITLE.search(name_clean):
        return CategoryEvaluation("mousepad", 0.85, "Explicit mousepad / deskmat in title")
    if RE_DONGLE_TITLE.search(name_clean):
        return CategoryEvaluation("dongle", 0.85, "Explicit dongle / receiver in title")

    # 4. Check Audio & Microphones explicitly in title
    if RE_AUDIO.search(name_clean):
        return CategoryEvaluation("headset", 0.85, "Explicit audio in title")
    if RE_MIC.search(name_clean):
        return CategoryEvaluation("microphone", 0.85, "Explicit microphone in title")

    # 5. Check if vendor product_type or collection is explicitly provided
    if product_type:
        pt_clean = product_type.lower()
        if "keyboard" in pt_clean:
            return CategoryEvaluation("keyboard", 0.85, f"Vendor product_type: {product_type}")
        if "mouse" in pt_clean or "mice" in pt_clean:
            return CategoryEvaluation("mouse", 0.85, f"Vendor product_type: {product_type}")
        if "headphone" in pt_clean or "headset" in pt_clean or "earphone" in pt_clean or "audio" in pt_clean:
            return CategoryEvaluation("headset", 0.85, f"Vendor product_type: {product_type}")
        if "microphone" in pt_clean or "mic" in pt_clean:
            return CategoryEvaluation("microphone", 0.85, f"Vendor product_type: {product_type}")
        if "keycap" in pt_clean:
            return CategoryEvaluation("keycap", 0.85, f"Vendor product_type: {product_type}")
        if "switch" in pt_clean:
            return CategoryEvaluation("switch", 0.85, f"Vendor product_type: {product_type}")
        if "cable" in pt_clean:
            return CategoryEvaluation("cable", 0.85, f"Vendor product_type: {product_type}")

    # 5b. Check if Complete DIY / Barebone Keyboard Kit
    if RE_KEYBOARD_KITS.search(name_clean) and not RE_COMPONENT_KITS.search(name_clean):
        return CategoryEvaluation("keyboard", 0.85, "Complete DIY / Barebone Keyboard Kit")

    # 6. Known Model Families Matching (High Confidence)
    if RE_MOUSE_MODELS.search(name_clean):
        return CategoryEvaluation("mouse", 0.75, f"Matched known mouse model family in '{name_clean}'")
    if RE_KEYBOARD_MODELS.search(name_clean):
        return CategoryEvaluation("keyboard", 0.75, f"Matched known keyboard model family in '{name_clean}'")

    # 7. URL Semantics
    if product_url:
        url_lower = product_url.lower()
        if "mouse" in url_lower or "mice" in url_lower:
            return CategoryEvaluation("mouse", 0.70, "URL semantic contains mouse")
        if "keyboard" in url_lower:
            return CategoryEvaluation("keyboard", 0.70, "URL semantic contains keyboard")
        if "keycap" in url_lower:
            return CategoryEvaluation("keycap", 0.70, "URL semantic contains keycap")
        if "switch" in url_lower:
            return CategoryEvaluation("switch", 0.70, "URL semantic contains switch")
        if "cable" in url_lower:
            return CategoryEvaluation("cable", 0.70, "URL semantic contains cable")

    # 8. Device patterns in name
    if RE_MOUSE.search(name_clean):
        return CategoryEvaluation("mouse", 0.65, "Mouse keywords in title")
    if RE_KEYBOARD.search(name_clean):
        return CategoryEvaluation("keyboard", 0.65, "Keyboard keywords in title")

    # 9. Fallback specs/tags in full_text
    if RE_KEYCAP_TITLE.search(full_text):
        return CategoryEvaluation("keycap", 0.40, "Keycap specs in tags/description")
    if RE_SWITCH_TITLE.search(full_text):
        return CategoryEvaluation("switch", 0.40, "Switch specs in tags/description")
    if RE_CABLE_TITLE.search(full_text):
        return CategoryEvaluation("cable", 0.40, "Cable specs in tags/description")
    if RE_ACCESSORY_TITLE.search(full_text):
        return CategoryEvaluation("accessory", 0.40, "Accessory specs in tags/description")
    if RE_MOUSE.search(full_text):
        return CategoryEvaluation("mouse", 0.40, "Mouse keywords in full text")
    if RE_KEYBOARD.search(full_text):
        return CategoryEvaluation("keyboard", 0.40, "Keyboard keywords in full text")

    return CategoryEvaluation("other", 0.10, "No strong category signals found")


def detect_category(name: str, description: str = "", extra_text: str = "", tags: list[str] | None = None) -> str:
    """Wrapper returning the category string for backward compatibility."""
    return evaluate_category(name, description=description, product_url=extra_text, tags=tags).category


def is_hardware_device(category: str) -> bool:
    """Return True if category represents an active individual peripheral device with HID/firmware."""
    return category in {"keyboard", "mouse", "headset", "microphone"}


def normalize_product_name(vendor: str, raw_name: str) -> str:
    """
    Produce a clean canonical model name while preserving pristine vendor display spelling.
    E.g. 'AULA F75 75% Wireless Tri-Mode Gasket Mechanical Keyboard' -> 'F75'
    'EPOMAKER Galaxy70 Custom Mechanical Keyboard' -> 'Galaxy70'
    """
    if not raw_name:
        return "Unknown"

    cleaned = raw_name.strip()

    # Remove vendor prefix if present
    cleaned_no_vendor = re.sub(rf'^{vendor}\s*[-/:]?\s*', '', cleaned, flags=re.IGNORECASE).strip()
    if cleaned_no_vendor:
        cleaned = cleaned_no_vendor

    # Strip bracketed text like [NEW], (Driver), [2024 Version]
    cleaned = re.sub(r'\[.*?\]|\(.*?\)', ' ', cleaned)

    tokens = RE_WHITESPACE.split(cleaned)
    kept_tokens = []

    for t in tokens:
        t_clean = RE_CLEAN_PUNCT.sub('', t).strip()
        if not t_clean:
            continue
        if re.match(RE_STOPWORDS, t_clean) and t_clean.upper() not in {"HE", "MAX", "PRO", "PLUS", "ULTRA", "V2", "V3", "AIR", "L3", "F1", "X1", "R1"}:
            continue
        kept_tokens.append(t_clean)

    canonical = " ".join(kept_tokens).strip()
    if not canonical:
        canonical = cleaned

    return canonical


def generate_identity_key(vendor: str, name: str) -> str:
    """
    Generate a normalized, whitespace-agnostic matching key for exact model deduplication.
    E.g. 'AULA F75', 'AULA F 75', 'F75' -> 'f75'
    'EPOMAKER Galaxy70', 'Galaxy 70' -> 'galaxy70'
    'AULA HERO 84 HE', 'AULA HERO84 HE' -> 'hero84he'
    """
    cleaned = name.strip()
    cleaned = re.sub(rf'^{vendor}\s*[-/:]?\s*', '', cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\[.*?\]|\(.*?\)', ' ', cleaned)
    tokens = RE_WHITESPACE.split(cleaned)
    kept_tokens = []
    for t in tokens:
        t_clean = RE_CLEAN_PUNCT.sub('', t).strip()
        if not t_clean:
            continue
        if re.match(RE_STOPWORDS, t_clean) and t_clean.upper() not in {"HE", "MAX", "PRO", "PLUS", "ULTRA", "V2", "V3", "AIR", "L3", "F1", "X1", "R1"}:
            continue
        kept_tokens.append(t_clean)
    
    key_str = "".join(kept_tokens).lower()
    key_str = re.sub(r'[\s\-_+.]', '', key_str)
    return key_str or simplify_name(name)


def simplify_name(name: str) -> str:
    """Normalize string for fuzzy comparison."""
    return re.sub(r'[\s\-_+.]', '', name).lower()
