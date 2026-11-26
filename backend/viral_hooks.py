"""Optional AI-written openings for campaign videos.

Only the spoken hook and the cover cue are written here. The product facts,
the demonstration detail and the closing line always come from what the owner
saved, so no new claim can enter a video. Without OPENAI_API_KEY this module
does nothing and the built-in openings are used.
"""
import json
import logging
import os
import re

log = logging.getLogger(__name__)

PROMPT = """You are writing the first two seconds of a 15-second vertical video ad
for Pinterest, YouTube Shorts, TikTok and Reels.

PRODUCT: {name}
VERIFIED FACT (the next line of the video says this; do not restate or add to it): {feature}
OPENINGS ALREADY USED (write something clearly different): {earlier}

Write ONE spoken hook that stops the scroll, 12 words or fewer, using ONE of these angles:
- a surprising question the buyer would ask
- a "what if" about someone they love
- a before-and-after in one sentence
- a "nobody tells you this" lead-in to the verified fact

Also write COVER TEXT: 2 to 4 words that create curiosity.

RULES
- Talk like a real person telling a friend. No hype words such as "amazing" or "game-changer".
- Make no claim about the product. No prices, numbers, guarantees, results or "best".
- No hashtags, emoji, links or quotation marks.

Return only JSON: {{"hook": "...", "cover": "..."}}"""

_BLOCKED = re.compile(r"[\d$%#@<>{}\[\]\"“”]|https?:|www\.|\b(?:best|guarantee\w*|cure\w*|proven|free|"
                      r"amazing|game[- ]changer|miracle|risk[- ]free)\b", re.I)


def acceptable(text, most_words, most_chars):
    words = text.split()
    return (1 < len(words) <= most_words and len(text) <= most_chars and not _BLOCKED.search(text)
            and all(ch.isprintable() for ch in text))


def ask(prompt):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip(), timeout=25, max_retries=1)
    reply = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"), temperature=0.9, max_tokens=120,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}])
    return reply.choices[0].message.content or "{}"


def write(product, earlier=()):
    """Return {'hook','cover'} or None when the built-in opening should be used."""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None
    try:
        answer = json.loads(ask(PROMPT.format(name=product["name"], feature=product["feature"],
                                              earlier="; ".join(earlier[-12:]) or "none")))
        hook = re.sub(r"\s+", " ", str(answer["hook"])).strip()
        cover = re.sub(r"\s+", " ", str(answer["cover"])).strip()
    except Exception:
        log.warning("AI opening unavailable; using a built-in opening")
        return None
    if (not acceptable(hook, 14, 90) or not acceptable(cover, 4, 26)
            or hook.casefold() in {e.casefold() for e in earlier}):
        return None
    return {"hook": hook, "cover": cover}
