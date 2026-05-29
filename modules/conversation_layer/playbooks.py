"""Recruitment playbook primitives for shadow-mode reply generation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecruitmentSignals:
    focus: str
    temperature: str
    risk: str
    language: str
    wants_application: bool
    needs_trust_repair: bool
    asks_multiple_questions: bool


RESTRICTED_REPLY_KEYWORDS = (
    "employee salary",
    "কর্মীর বেতন",
    "bank account",
    "ব্যাংক একাউন্ট",
    "vessel name",
    "ship name",
    "lc number",
    "bl number",
    "profit",
    "loss",
    "owner personal",
)

SAFE_RECRUITMENT_MARKERS = (
    "চাকরি",
    "job",
    "survey scout",
    "সার্ভে স্কট",
    "security guard",
    "সিকিউরিটি গার্ড",
    "বেতন",
    "salary",
    "যোগ্যতা",
    "training",
    "ট্রেনিং",
    "অফিস",
    "ভিক্টোরিয়া",
    "পাহাড়তলী",
    "নাম",
    "বয়স",
    "আবেদন",
)

FOCUS_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("salary", ("salary", "বেতন", "মাসে", "কত পাব", "টাকা")),
    ("fee", ("fee", "ফি", "জামানত", "joining fee", "টাকা লাগবে", "ট্রেনিং ফি")),
    ("documents", ("কাগজ", "document", "nid", "ছবি", "সার্টিফিকেট", "কি লাগবে")),
    ("office_location", ("ঠিকানা", "location", "অফিস", "কোথায়", "address", "একে খান")),
    ("training", ("training", "ট্রেনিং", "শিখ", "অভিজ্ঞতা", "experience")),
    ("ship_duty", ("জাহাজ", "ship", "ডিউটি", "duty", "route", "থাকা", "খাবার")),
    ("trust", ("fake", "ভুয়া", "বাটপার", "fraud", "scam", "প্রতার")),
    ("application", ("apply", "আবেদন", "করতে চাই", "interested", "আগ্রহী", "জয়েন", "join")),
)

CANONICAL_FACTS: dict[str, str] = {
    "salary": "প্রশিক্ষণে সাধারণত ১০,০০০-১৫,০০০ টাকা, পরে কাজ/অভিজ্ঞতা অনুযায়ী ১২,০০০-১৮,০০০+ টাকা হতে পারে। নির্দিষ্ট বেতন অফিস যাচাইয়ের পর ঠিক হয়।",
    "fee": "পুরনো ক্যানোনিক্যাল নোটে ৩,৫০০ টাকা যোগদান খরচের কথা আছে, কিন্তু বর্তমান কোর KB-তে কিছু উত্তর ফি নেই বলছে। এই অসামঞ্জস্য owner review ছাড়া auto-send করা নিরাপদ নয়।",
    "documents": "সাধারণত NID/জন্ম নিবন্ধন, ছবি, সার্টিফিকেট থাকলে সেটি, চেয়ারম্যান/কমিশনার সনদ এবং অভিভাবকের NID কপি চাওয়া হয়।",
    "office_location": "অফিস: ভিক্টোরিয়া গেইট/একে খান মোড়, পাহাড়তলী, চট্টগ্রাম। অফিস সময় বর্তমান কোরে সকাল ৯টা-৫টা বলা আছে।",
    "training": "নতুনদের জন্য প্রায় ৪৫ দিনের প্রশিক্ষণ আছে। অভিজ্ঞতা না থাকলেও আবেদন করা যায়।",
    "ship_duty": "Survey Scout/Escort কাজে জাহাজে মালামাল তদারকি, লোড-আনলোড হিসাব, চুরি/ক্ষতি রোধ এবং রিপোর্টিং থাকে। থাকা সাধারণত জাহাজে/অফিসে ফ্রি, খাবার নিজ খরচে মেস সিস্টেমে।",
    "trust": "এটি বেসরকারি প্রতিষ্ঠান। সন্দেহ থাকলে সরাসরি অফিসে এসে যাচাই করে সিদ্ধান্ত নিতে বলা নিরাপদ।",
    "application": "আবেদনের জন্য নাম, বয়স, শিক্ষা, বর্তমান ঠিকানা/জেলা, অভিজ্ঞতা এবং যোগাযোগ নম্বর সংগ্রহ করা দরকার।",
    "general": "Survey Scout/Escort/Security Guard নিয়োগে সততা, দায়িত্বশীলতা ও শারীরিক সক্ষমতা গুরুত্বপূর্ণ। নতুনদেরও আবেদন করার সুযোগ আছে।",
}

APPLICATION_FIELDS = "নাম, বয়স, শিক্ষা, জেলা/বর্তমান ঠিকানা, অভিজ্ঞতা এবং মোবাইল নম্বর"


def analyze_recruitment_signals(text: str) -> RecruitmentSignals:
    lowered = text.lower().strip()
    focus = "general"
    matches = 0
    for name, keywords in FOCUS_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            if focus == "general":
                focus = name
            matches += 1

    hot_words = ("apply", "করতে চাই", "জয়েন", "join", "ready", "confirm", "আগ্রহী")
    warm_words = ("details", "বিস্তারিত", "salary", "বেতন", "কাজ", "job", "কোথায়", "কিভাবে")
    risk_words = ("fake", "ভুয়া", "বাটপার", "fraud", "scam", "প্রতার")

    temperature = "cold"
    if any(word in lowered for word in hot_words):
        temperature = "hot"
    elif any(word in lowered for word in warm_words):
        temperature = "warm"

    risk = "trust" if any(word in lowered for word in risk_words) else "normal"
    language = "en" if lowered.isascii() else "bn"

    return RecruitmentSignals(
        focus=focus,
        temperature=temperature,
        risk=risk,
        language=language,
        wants_application=focus == "application" or temperature == "hot",
        needs_trust_repair=risk == "trust" or focus == "trust",
        asks_multiple_questions=matches > 1 or text.count("?") > 1,
    )


def classify_reply_safety(reply: str) -> str:
    lowered = reply.lower()
    if any(keyword in lowered for keyword in RESTRICTED_REPLY_KEYWORDS):
        return "restricted"
    if any(marker in lowered for marker in SAFE_RECRUITMENT_MARKERS):
        return "safe"
    if len(reply.strip()) <= 160 and any(greeting in lowered for greeting in ("আসসালাম", "ওয়ালাইকুম", "ধন্যবাদ")):
        return "safe"
    return "review"


def build_rule_reply(signals: RecruitmentSignals) -> str:
    fact = CANONICAL_FACTS.get(signals.focus, CANONICAL_FACTS["general"])

    if signals.needs_trust_repair:
        return (
            "ভাই, সন্দেহ হওয়া স্বাভাবিক। আমাদের অফিসে সরাসরি এসে যাচাই করে তারপর সিদ্ধান্ত নিতে পারেন।\n"
            f"{CANONICAL_FACTS['office_location']}\n"
            f"আগ্রহী হলে {APPLICATION_FIELDS} পাঠান।"
        )

    if signals.focus == "fee":
        return (
            "ফি/জামানত বিষয়ে বর্তমান তথ্য owner review ছাড়া নিশ্চিত করে বলা ঠিক হবে না।\n"
            "আপনি অফিসে সরাসরি যোগাযোগ করুন বা আগে নাম-বয়স-জেলা পাঠান, অফিস থেকে পরিষ্কারভাবে জানানো হবে।"
        )

    if signals.wants_application:
        return (
            "ধন্যবাদ ভাই। আবেদন শুরু করতে এই তথ্যগুলো পাঠান: "
            f"{APPLICATION_FIELDS}.\n"
            "নতুন হলেও আবেদন করা যাবে; অফিস যাচাই করে পরের ধাপ জানাবে।"
        )

    return f"{fact}\nআগ্রহী হলে {APPLICATION_FIELDS} পাঠান, অফিস থেকে পরের ধাপ জানানো হবে।"
