"""
OpenBell Voice Assistant — Response texts & intent keywords

British female persona. Never answers police questions.
"""

# ── Intent enum ──
DELIVERY = "delivery"
BUSINESS = "business"
PERSONAL = "personal"
POLICE = "police"
UNKNOWN = "unknown"
SILENCE = "silence"

# ── Greeting ──
GREETING = (
    "Hello! We're not available at the moment. "
    "Could you let me know how I can help? "
    "Are you delivering a package, or is there something else?"
)

# ── Responses per intent ──
RESPONSES = {
    DELIVERY: (
        "Lovely, thank you! Please pop the package in the large pot "
        "by the door. Cheers!"
    ),
    BUSINESS: (
        "Thanks for stopping by. Would you mind leaving a business card? "
        "We'll get back to you."
    ),
    PERSONAL: (
        "Thanks for coming round! Please give us a text or a call "
        "and we'll get back to you as soon as we can."
    ),
    POLICE: (
        "I'm sorry, I'm not able to help with any enquiries at the moment. "
        "You're welcome to leave a card with your contact details, "
        "or you can get in touch by phone. Thank you."
    ),
    UNKNOWN: (
        "Thanks for stopping by. Please leave a card or give us a text, "
        "and we'll get back to you. Cheers!"
    ),
    SILENCE: (
        "It doesn't seem like anyone's there. "
        "If you need us, please give us a text or a call. Bye for now!"
    ),
}

# ── Follow-up (after a second unrecognised turn) ──
FOLLOWUP = (
    "I'm sorry, I didn't quite catch that. "
    "You're welcome to leave a card or give us a ring. Have a lovely day!"
)

# ── Farewell ──
FAREWELL = "Cheers, bye!"

# ── Intent keywords ──
INTENT_KEYWORDS = {
    DELIVERY: [
        "package", "parcel", "delivery", "delivering", "deliver",
        "amazon", "fedex", "ups", "dhl", "courier", "post", "postman",
        "mailman", "drop off", "dropping off", "dropoff", "hermes",
        "royal mail", "signed for", "sign for", "leave this",
    ],
    BUSINESS: [
        "business", "card", "selling", "sell", "offer", "deal",
        "survey", "insurance", "solar", "windows", "roof", "roofing",
        "campaign", "petition", "canvass", "charity", "donate",
        "appointment", "estimate", "quote", "representative",
        "broadband", "energy", "gas", "electric",
    ],
    POLICE: [
        "police", "officer", "detective", "sergeant", "constable",
        "warrant", "investigation", "law enforcement", "cid",
        "enquiry", "enquiries", "incident", "crime",
        "badge", "identification", "id please",
    ],
    PERSONAL: [
        "friend", "friends", "visit", "visiting", "came to see",
        "stopping by", "stopped by", "looking for", "is home",
        "are home", "around", "pop round", "popped round",
        "hello is", "hi is", "neighbour", "neighbor",
    ],
}
