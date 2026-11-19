"""Quiet, evidence-bound advertising copy editor.

The customer never sees this production role. Every submitted HeyGen script
must match the actual chosen product and the verified facts used to compose it.
Calls no paid provider and does not invent discounts, features, or a URL.
"""
import re
from fastapi import HTTPException


def closing_cta(product_name):
    name = str(product_name or "").casefold()
    if re.search(r"\b(?:thermoflask|coffee|mug|tumbler|flask)\b", name):
        lead = "Coffee break?"
    elif re.search(r"\b(?:baking|pastry|bake|silicone mat)\b", name):
        lead = "Next batch?"
    elif re.search(r"\b(?:organizer|drawer|storage|divider)\b", name):
        lead = "Less clutter?"
    elif re.search(r"\b(?:lamp|night light|lighting)\b", name):
        lead = "Bright idea?"
    else:
        lead = "Curious?"
    # Platform links in MP4s are not clickable; never fabricate a sale or price.
    return lead + " Find the exact product link in the post caption."


def editorial_review(product_name, plan, narration, *, max_words=34):
    """Fail closed if an ad script departs from grounded authored copy.

    Uses the canonical script compiled from exact product identity and seller
    evidence. This isn't semantic recognition of video scenes or audio.
    """
    from backend.heygen_video_api import _spoken_identity, _natural_product_reference
    from backend.heygen_video_api import _supported_problem_hook, _short_seller_facts
    from backend.sales_script import is_distinct_product_fact
    name = str(product_name or "").strip()
    plan = plan or {}
    text = re.sub(r"\s+", " ", str(narration or "")).strip()
    identity = _spoken_identity(name)
    product = _natural_product_reference(identity)
    cta = closing_cta(name)
    if not name or not product:
        raise HTTPException(422, "Video script needs a specific verified product.")
    if not text.endswith(cta) or text.count("Find the exact product link") != 1:
        raise HTTPException(422, "Video script needs one complete and relevant closing call to action.")
    if len(text.split()) > max_words:
        raise HTTPException(422, "Video script is too long for its narration duration.")
    if re.search(r"\b(?:free shipping|discount|on sale|best seller|guaranteed|cure|save \d+%)\b",text,re.I):
        raise HTTPException(422, "Video script contains an unverified marketing promise.")
    if not re.search(re.escape(product), text, re.I):
        raise HTTPException(422, "Video script does not identify the product actually being advertised.")
    # A newly arrived or altered script must be one of the fact-grounded
    # templates the production author is allowed to make.
    hook = _supported_problem_hook(name, identity, plan)
    options = []
    if hook:
        options.append(" ".join([*hook,cta]))
        # Concise alternative only when the full hook exceeds narration length.
        options.append(" ".join([hook[0],hook[2],cta]))
    evidence = []
    for item in (plan.get("evidence") or []):
        if not isinstance(item,dict):
            continue
        for phrase in _short_seller_facts(item.get("text") or ""):
            if is_distinct_product_fact(phrase,name) and phrase not in evidence:
                evidence.append(phrase)
    opening = f"Looking for a simpler solution? Take a look at this {product}."
    options.append(" ".join([opening, cta]))
    options.extend(" ".join([opening, fact, cta]) for fact in evidence)
    if text not in options:
        raise HTTPException(422,"Video script describes an unverified feature or differs from the selected product's approved copy.")
    return {"approved": True,"script":text,"product_name":name,"word_count":len(text.split()),
            "cta":cta,"evidence_checked":True,"paid_calls":0}
