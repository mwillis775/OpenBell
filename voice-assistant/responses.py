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
    "Hello there! Nobody's available to come to the door right now, "
    "but I'm happy to help. Are you delivering something, "
    "or is there something else I can do for you?"
)

# ── Responses per intent ──
RESPONSES = {
    DELIVERY: (
        "Lovely, thank you! If you could just pop it in the large pot "
        "by the front door, that would be brilliant. Cheers!"
    ),
    BUSINESS: (
        "Thanks for stopping by! Would you mind leaving a business card "
        "or leaflet? Someone will get back to you. Cheers!"
    ),
    PERSONAL: (
        "Ah, thanks for coming round! Best bet is to send a text or "
        "give a ring, and someone will get back to you. Cheers!"
    ),
    POLICE: (
        "I appreciate you stopping by, but I'm not able to help with "
        "any enquiries at the moment. You're very welcome to leave a "
        "card with your contact details. Thank you."
    ),
    UNKNOWN: (
        "Thanks for stopping by! If you'd like to leave a card or "
        "send a text, someone will get back to you. Cheers!"
    ),
    SILENCE: (
        "Hello? It doesn't seem like anyone's there. "
        "If you need anything, just give us a text or a call. Bye for now!"
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
        "evri", "yodel", "dpd", "tnt", "usps", "collect", "pickup",
        "pick up", "food", "order", "groceries", "instacart",
        "doordash", "uber eats", "just eat", "deliveroo", "grubhub",
    ],
    BUSINESS: [
        "business", "card", "selling", "sell", "offer", "deal",
        "survey", "insurance", "solar", "windows", "roof", "roofing",
        "campaign", "petition", "canvass", "charity", "donate",
        "appointment", "estimate", "quote", "representative",
        "broadband", "energy", "gas", "electric", "fiber", "fibre",
        "salesman", "saleswoman", "salesperson", "pamphlet",
        "flyer", "leaflet", "brochure", "contractor", "plumber",
        "landscap", "gutter", "pest control", "exterminator",
    ],
    POLICE: [
        "police", "officer", "detective", "sergeant", "constable",
        "warrant", "investigation", "law enforcement", "cid",
        "enquiry", "enquiries", "incident", "crime",
        "badge", "identification", "id please",
        "fbi", "federal", "marshal", "sheriff", "deputy",
        "inspector", "lieutenant", "captain", "authorities",
        "search warrant", "arrest", "suspect", "witness",
        "department", "precinct", "station",
    ],
    PERSONAL: [
        "friend", "friends", "visit", "visiting", "came to see",
        "stopping by", "stopped by", "looking for", "is home",
        "are home", "around", "pop round", "popped round",
        "hello is", "hi is", "neighbour", "neighbor",
        "family", "relative", "uncle", "aunt", "cousin",
        "brother", "sister", "mum", "dad", "mom",
        "know them", "know you", "invited", "expecting me",
    ],
}
