"""30-day organic TikTok growth plan: ideas, scripts, checks and result advice.

Everything here is plain data and pure functions. Nothing calls a paid service,
posts anywhere or contacts TikTok. The owner films or renders, reviews, and
posts each video personally.
"""
import re

ACCOUNT = "4your Good Health"
HANDLE = "@hammer2085"
PROFILE = "https://www.tiktok.com/@hammer2085"
START_FOLLOWERS = 55
GOAL_FOLLOWERS = 1000
DAYS = 30

CATEGORIES = {
    "gadgets": "Amazing gadgets and product discoveries",
    "kitchen": "Kitchen and household hacks",
    "health": "Healthy lifestyle tips",
    "money": "Money-saving ideas",
    "books": "Books and personal development",
}

FORMATS = {
    "demo": ("Demonstration", "Show the thing working from start to finish in one take. Say what you are doing as you do it."),
    "before_after": ("Before and after", "Open on the problem, show the fix, end on the result. Use the same camera angle for both."),
    "quick_tip": ("Quick tip", "One tip, one reason, one example. Under 20 seconds."),
    "comparison": ("Comparison", "Put two options side by side and show the difference the viewer can see."),
    "story": ("Personal story", "Tell what happened to you, in order. Say only what is true for you."),
    "discovery": ("Product discovery", "Show the product, the one feature that matters, and it being used. No claims you do not show."),
}

# (day, category, format, topic, hook, cover text, keywords, scenes, caption, hashtags)
# A scene is (what to say, what to show, on-screen text). Scene 4 always ends
# with a plain invitation that names the account.
_PLAN = [
 (1, "gadgets", "demo", "Jar opener that saves your grip",
  "This jar has beaten me twice this week.", "Stuck lid?", ["jar", "lid"],
  [("This jar has beaten me twice this week.", "Close-up of your hands failing to twist a tight jar lid.", "Stuck lid?"),
   ("So I tried this little jar opener.", "Hold the jar opener up to the camera, then set it on the lid.", "The jar opener"),
   ("One twist and the lid pops. No towel, no banging it on the counter.", "One take of the opener turning the lid until it pops.", "One twist"),
   ("Follow 4your Good Health for more small gadgets that earn their drawer space.", "Open jar on the counter with the opener beside it.", "Follow for more finds")],
  "A jar opener that actually opens the jar. Shown in one take.", ["#kitchengadgets", "#jaropener", "#productfinds", "#homehacks"]),
 (2, "kitchen", "demo", "Keep fresh herbs in a jar of water",
  "Stop letting your herbs wilt in the bag.", "Wilted herbs?", ["herbs", "water"],
  [("Stop letting your herbs wilt in the bag.", "Limp bunch of herbs in its plastic bag on the counter.", "Wilted herbs?"),
   ("Trim the stems and stand them in a jar with a little water.", "Scissors trimming the stem ends, then herbs going into a jar of water.", "Trim. Jar. Water."),
   ("Loosely cover the top and keep the jar in the fridge door.", "Cover the leaves with the bag and place the jar in the fridge door.", "Fridge door"),
   ("Save this for your next grocery run, and follow 4your Good Health for more kitchen hacks.", "The jar of herbs standing upright in the fridge.", "Save for grocery day")],
  "How I store fresh herbs: trimmed stems, jar of water, fridge door.", ["#kitchenhacks", "#freshherbs", "#foodstorage", "#cookingtips"]),
 (3, "health", "quick_tip", "Keep your water bottle where you can see it",
  "I drink more water when I can see it.", "My water habit", ["water", "bottle"],
  [("I drink more water when I can see it.", "You at your desk or counter, picking up a full water bottle.", "My water habit"),
   ("So my bottle lives right next to my keys, not in a cabinet.", "Set the bottle down beside your keys or phone.", "Keep it in sight"),
   ("I fill it once in the morning and once after lunch.", "Fill the bottle at the tap.", "Fill it twice"),
   ("What helps you drink more water? Tell me, and follow 4your Good Health for more simple habits.", "You holding the bottle and smiling at the camera.", "What works for you?")],
  "A habit that works for me: keep the water bottle where I can see it.", ["#healthyhabits", "#waterbottle", "#simplehabits", "#wellness"]),
 (4, "gadgets", "discovery", "Stick-on motion-sensor night light",
  "No more feeling for the light switch at 2 a.m.", "Dark hallway?", ["light", "motion"],
  [("No more feeling for the light switch at two a.m.", "Dark hallway or closet, your hand reaching for a switch.", "Dark hallway?"),
   ("This is a stick-on motion-sensor light. No wiring.", "Hold the light up, then press it onto the wall or shelf.", "Stick-on light"),
   ("Walk past, and the light comes on by itself.", "Walk into frame in the dark so the light switches on.", "It turns on for you"),
   ("Follow 4your Good Health for more useful finds like this one.", "The lit hallway or closet.", "Follow for more finds")],
  "A stick-on motion-sensor light for a dark hallway or closet.", ["#productfinds", "#homegadgets", "#nightlight", "#homehacks"]),
 (5, "books", "discovery", "A Season for Everyone: a look inside",
  "Who makes you smile before they even say hello?", "A Christmas book", ["season", "christmas"],
  [("Who makes you smile before they even say hello?", "Hold A Season for Everyone up to the camera, cover first.", "A Christmas book"),
   ("This is my book, A Season for Everyone. Each coloring scene has a reflection page beside it.", "Open the book to a coloring page with its facing prompt page.", "Color, then reflect"),
   ("You color the Christmas scene, then write your own memory next to it.", "Your hand coloring part of a scene, then writing on the prompt page.", "Make the memory yours"),
   ("Follow 4your Good Health for more books worth your quiet time.", "The closed book on a table with colored pencils.", "Follow for more books")],
  "A look inside my Christmas coloring and reflection book, A Season for Everyone.", ["#christmasbooks", "#coloringbook", "#booktok", "#journaling"]),
 (6, "kitchen", "demo", "Freeze leftover broth in an ice cube tray",
  "Don't pour that leftover broth down the sink.", "Leftover broth?", ["broth", "tray"],
  [("Don't pour that leftover broth down the sink.", "A half-used carton or pot of broth on the counter.", "Leftover broth?"),
   ("Pour it into an ice cube tray and freeze it.", "Pour broth into the tray, then slide the tray into the freezer.", "Pour and freeze"),
   ("Next time you cook, drop in a cube or two.", "Drop frozen cubes into a warm pan or pot.", "One cube at a time"),
   ("Save this one, and follow 4your Good Health for more kitchen hacks.", "A freezer bag of broth cubes.", "Save for later")],
  "Freeze leftover broth in an ice cube tray and use a cube at a time.", ["#kitchenhacks", "#foodwaste", "#mealprep", "#cookingtips"]),
 (7, "gadgets", "comparison", "Kitchen scale versus measuring cup",
  "A cup of flour is not always a cup of flour.", "Cup vs. scale", ["scale", "flour"],
  [("A cup of flour is not always a cup of flour.", "Scoop a packed cup of flour and level it.", "Cup vs. scale"),
   ("Watch. I'll weigh a packed cup, then a loose one.", "Weigh the packed cup on a kitchen scale, then a loosely filled cup.", "Packed, then loose"),
   ("Same cup, two different numbers on the scale.", "Close-up of each reading on the scale display.", "Two different weights"),
   ("Follow 4your Good Health for more kitchen gadgets worth owning.", "The scale on the counter beside the cup.", "Follow for more")],
  "Same measuring cup, two different weights. Why I use a kitchen scale.", ["#kitchengadgets", "#bakingtips", "#kitchenscale", "#productfinds"]),
 (8, "health", "story", "A ten-minute walk after dinner",
  "I used to go straight from dinner to the couch.", "After dinner", ["walk", "dinner"],
  [("I used to go straight from dinner to the couch.", "You getting up from the dinner table.", "After dinner"),
   ("Now I put my shoes on and walk for ten minutes.", "Lace up shoes by the door and step outside.", "Ten minutes"),
   ("It's my time to unwind, and it's short enough that I actually do it.", "You walking down your street, filmed from the side or in selfie view.", "Short enough to keep"),
   ("What's your after-dinner habit? Follow 4your Good Health for more simple ones.", "Back at your door, a quick wave to the camera.", "Your habit?")],
  "My after-dinner habit: shoes on, ten minutes outside.", ["#healthyhabits", "#walking", "#eveningroutine", "#wellness"]),
 (9, "money", "comparison", "Read the unit price, not the big price",
  "The bigger box is not always the better deal.", "Which is cheaper?", ["unit price", "shelf"],
  [("The bigger box is not always the better deal.", "Two sizes of the same product on a store shelf or your counter.", "Which is cheaper?"),
   ("Look for the small unit price on the shelf tag.", "Point to the unit price on each shelf tag, or show both receipts.", "Find the unit price"),
   ("Compare those two numbers. The lower one is the better buy.", "Close-up of both unit prices side by side.", "Lower number wins"),
   ("Share this with whoever does your shopping, and follow 4your Good Health for more money savers.", "You putting the better buy in the cart.", "Share with your shopper")],
  "Check the unit price on the shelf tag before you pick a size.", ["#moneysavingtips", "#groceryshopping", "#budgeting", "#savemoney"]),
 (10, "gadgets", "demo", "Handheld milk frother",
  "Coffee-shop foam, made at my kitchen counter.", "Foam at home", ["frother", "milk"],
  [("Coffee-shop foam, made at my kitchen counter.", "A plain cup of coffee beside a small cup of milk.", "Foam at home"),
   ("This is a handheld milk frother.", "Hold the frother up, then lower it into the milk.", "Handheld frother"),
   ("A few seconds in warm milk, and it's foam.", "The milk rising into foam in one continuous shot.", "A few seconds"),
   ("Follow 4your Good Health for more little gadgets that make mornings better.", "Pour the foam onto the coffee.", "Follow for more")],
  "A handheld milk frother turning warm milk into foam.", ["#coffeetok", "#kitchengadgets", "#milkfrother", "#productfinds"]),
 (11, "kitchen", "before_after", "Steam-clean the microwave with lemon water",
  "My microwave looked like this.", "Messy microwave", ["microwave", "lemon"],
  [("My microwave looked like this.", "Inside of the splattered microwave.", "Messy microwave"),
   ("I heated a bowl of water with lemon slices for a few minutes.", "Bowl of water and lemon slices going into the microwave.", "Water and lemon"),
   ("Then I let it sit, and the mess wiped right off.", "Wipe the inside with a cloth in one pass.", "Wipe it down"),
   ("Save this for cleaning day, and follow 4your Good Health for more household hacks.", "The clean microwave interior.", "Save for cleaning day")],
  "Bowl of water, lemon slices, a few minutes, then wipe. Careful, the bowl is hot.", ["#cleaninghacks", "#cleantok", "#kitchenhacks", "#homehacks"]),
 (12, "gadgets", "comparison", "Vegetable chopper versus a knife",
  "One onion, two ways. Let's time it.", "Chopper vs. knife", ["chopper", "onion"],
  [("One onion, two ways. Let's time it.", "Two onion halves on a cutting board with a timer in view.", "Chopper vs. knife"),
   ("First half with my knife.", "Dice the first half with a knife while the timer runs.", "By knife"),
   ("Second half with the chopper.", "Push the second half through the vegetable chopper while the timer runs.", "By chopper"),
   ("You saw both times. Follow 4your Good Health for more honest gadget tests.", "Both piles of onion and the two times side by side.", "You saw the times")],
  "I timed one onion two ways: knife, then vegetable chopper.", ["#kitchengadgets", "#vegetablechopper", "#mealprep", "#productfinds"]),
 (13, "health", "quick_tip", "Put fruit at eye level in the fridge",
  "I eat what I see first.", "Eye-level fruit", ["fruit", "fridge"],
  [("I eat what I see first.", "Open the fridge door from the viewer's point of view.", "Eye-level fruit"),
   ("So I moved the fruit to the eye-level shelf in my fridge.", "Move a bowl of washed fruit from the drawer to the middle shelf.", "Move it up"),
   ("I wash it when I get home, so it's ready to grab.", "Rinse fruit in a colander and put it in a clear bowl.", "Washed and ready"),
   ("Save this, and follow 4your Good Health for more simple habits.", "You taking a piece of fruit from the bowl.", "Save this one")],
  "A small fridge change I made: washed fruit at eye level.", ["#healthyhabits", "#fridgeorganization", "#simplehabits", "#healthyeating"]),
 (14, "books", "discovery", "My Season to Shine coloring journal",
  "Ten quiet minutes, one page, one kind thought.", "A quiet moment", ["season to shine", "coloring"],
  [("Ten quiet minutes, one page, one kind thought.", "A printed page of My Season to Shine with colored pencils beside it.", "A quiet moment"),
   ("This is My Season to Shine, my printable Christmas coloring journal.", "Hold up the printed cover page.", "My Season to Shine"),
   ("Each scene comes with an affirmation and a prompt to write about.", "Color part of a scene, then point to the affirmation and the prompt.", "Color and write"),
   ("Follow 4your Good Health for more ways to slow down this season.", "The finished page on the table.", "Follow for more")],
  "A printable Christmas coloring journal with affirmations and prompts.", ["#coloringjournal", "#christmasprintables", "#selfcare", "#booktok"]),
 (15, "gadgets", "before_after", "Cable clips for a tangled desk",
  "This is what was living behind my desk.", "Cable mess", ["cable", "desk"],
  [("This is what was living behind my desk.", "The tangle of cords behind or on your desk.", "Cable mess"),
   ("These are stick-on cable clips.", "Hold the clips up, then press one onto the desk edge.", "Stick-on clips"),
   ("Each cord gets its own slot, so nothing slides off the desk.", "Press each cable into its clip, one by one.", "One slot per cord"),
   ("Follow 4your Good Health for more small fixes like this.", "Same angle as the opening shot, now tidy.", "Follow for more")],
  "Before and after: stick-on cable clips on my desk.", ["#deskorganization", "#cablemanagement", "#productfinds", "#homeoffice"]),
 (16, "kitchen", "demo", "Stop a cutting board from sliding",
  "If your cutting board slides, do this first.", "Sliding board?", ["cutting board", "towel"],
  [("If your cutting board slides, do this first.", "Push the cutting board so it slides on the counter.", "Sliding board?"),
   ("Wet a dish towel and wring it out.", "Wet and wring a dish towel at the sink.", "Damp towel"),
   ("Lay it flat under the board. Now it stays put.", "Lay the towel down, set the board on top and push it. It does not move.", "It stays put"),
   ("Share this with someone who cooks, and follow 4your Good Health for more kitchen hacks.", "You chopping on the steady board.", "Share with a cook")],
  "A damp dish towel under the cutting board keeps it from sliding.", ["#kitchenhacks", "#cookingtips", "#kitchensafety", "#homecooking"]),
 (17, "money", "demo", "Borrow ebooks free with your library card",
  "I stopped paying for every book I read.", "Free ebooks", ["library", "card"],
  [("I stopped paying for every book I read.", "You holding your phone and your library card.", "Free ebooks"),
   ("My library card works in my library's free reading app.", "Screen recording or over-the-shoulder shot of signing in with the card number hidden.", "Library card + app"),
   ("I borrow ebooks and audiobooks right on my phone.", "Tap borrow on a title and open it.", "Borrow on your phone"),
   ("Ask your local library what they offer, and follow 4your Good Health for more money savers.", "The book open on your phone.", "Ask your library")],
  "Your library card may get you free ebooks and audiobooks. Ask your local library.", ["#moneysavingtips", "#librarycard", "#booktok", "#frugalliving"]),
 (18, "gadgets", "demo", "Silicone stretch lids",
  "I ran out of lids that fit. Again.", "No lid that fits?", ["lids", "bowl"],
  [("I ran out of lids that fit. Again.", "A bowl of leftovers and a drawer of mismatched lids.", "No lid that fits?"),
   ("These are silicone stretch lids.", "Hold up the set of stretch lids.", "Stretch lids"),
   ("Pick the closest size and stretch it over the bowl.", "Stretch a lid over the bowl in one take, then over a cut melon or can.", "Stretch and seal"),
   ("Follow 4your Good Health for more kitchen finds.", "The covered bowl going into the fridge.", "Follow for more")],
  "Silicone stretch lids on the bowls that lost their lids.", ["#kitchengadgets", "#foodstorage", "#productfinds", "#kitchenorganization"]),
 (19, "health", "quick_tip", "Set a bedtime alarm, not just a morning one",
  "I set an alarm to go to bed.", "Bedtime alarm", ["alarm", "bed"],
  [("I set an alarm to go to bed.", "Your phone showing an evening alarm.", "Bedtime alarm"),
   ("When it rings, I put my phone on the charger across the room.", "Plug the phone in away from the bed.", "Phone across the room"),
   ("Then I read a few pages until I'm sleepy.", "You in a chair or bed opening a book.", "A few pages"),
   ("What's your wind-down routine? Follow 4your Good Health for more simple habits.", "Lamp switching off.", "Your routine?")],
  "My wind-down routine starts with a bedtime alarm.", ["#eveningroutine", "#healthyhabits", "#sleeproutine", "#simplehabits"]),
 (20, "kitchen", "quick_tip", "Label leftovers with masking tape and the date",
  "Is this from Tuesday, or last Tuesday?", "Mystery leftovers", ["tape", "date"],
  [("Is this from Tuesday, or last Tuesday?", "Open the fridge and hold up an unlabeled container.", "Mystery leftovers"),
   ("I keep masking tape and a marker in the kitchen drawer.", "Pull tape and a marker out of the drawer.", "Tape and marker"),
   ("Write what it is and the date, and stick it on the lid.", "Write the food and date on tape and press it on the container.", "Name and date"),
   ("Save this, and follow 4your Good Health for more kitchen hacks.", "A row of labeled containers in the fridge.", "Save this one")],
  "Masking tape, marker, name and date on every leftover.", ["#kitchenhacks", "#fridgeorganization", "#foodstorage", "#mealprep"]),
 (21, "gadgets", "before_after", "Fabric shaver on a pilled sweater",
  "I almost gave this sweater away.", "Pilled sweater", ["shaver", "sweater"],
  [("I almost gave this sweater away.", "Close-up of the pilled fabric on a sweater.", "Pilled sweater"),
   ("This is a fabric shaver.", "Hold the fabric shaver up and switch it on.", "Fabric shaver"),
   ("I'll do half so you can see the difference.", "Run the shaver over one half of the sweater.", "Half and half"),
   ("Follow 4your Good Health for more finds that save what you already own.", "Both halves side by side in the same light.", "Follow for more")],
  "Half a sweater with a fabric shaver, half without.", ["#productfinds", "#fabricshaver", "#clothingcare", "#homehacks"]),
 (22, "health", "comparison", "Compare the labels on two cereal boxes",
  "Two cereals. Same shelf. Read the label with me.", "Read the label", ["label", "sugar"],
  [("Two cereals. Same shelf. Read the label with me.", "Two cereal boxes side by side, fronts facing the camera.", "Read the label"),
   ("Turn them around and find the added sugars line.", "Turn both boxes and point to the added sugars line on each.", "Added sugars line"),
   ("Check the serving size too, because the two boxes may not match.", "Point to the serving size on each label.", "Check serving size"),
   ("Now you know where to look. Follow 4your Good Health for more simple label tips.", "Both labels in one close shot.", "Now you know")],
  "Where to look on a cereal label: added sugars and serving size.", ["#nutritionlabel", "#groceryshopping", "#healthyeating", "#foodlabels"]),
 (23, "kitchen", "before_after", "Tension rod for spray bottles under the sink",
  "Under my sink was a pile of bottles.", "Under the sink", ["tension rod", "sink"],
  [("Under my sink was a pile of bottles.", "Open the cabinet under the sink to show the clutter.", "Under the sink"),
   ("I put a tension rod across the cabinet.", "Twist a tension rod into place across the cabinet.", "One tension rod"),
   ("Spray bottles hang from it by their triggers.", "Hang each spray bottle on the rod by its trigger.", "Hang the bottles"),
   ("Save this for the weekend, and follow 4your Good Health for more household hacks.", "Same angle as the opening shot, now with floor space clear.", "Save for the weekend")],
  "A tension rod under the sink to hang spray bottles.", ["#homeorganization", "#undersink", "#homehacks", "#cleantok"]),
 (24, "books", "story", "Why I wrote The Fasting Blueprint",
  "I tried several diets before I wrote this book.", "My story", ["fasting blueprint", "book"],
  [("I tried several diets before I wrote this book.", "You on camera holding The Fasting Blueprint.", "My story"),
   ("I lost 25 pounds with routines like the ones in it. That's my own experience, not a typical result.", "Open the book to a routine page.", "My own experience, not a typical result"),
   ("So I wrote down what I did, step by step, in The Fasting Blueprint.", "Turn through a few pages slowly.", "What I did, step by step"),
   ("Talk with your doctor before you change how you eat. Follow 4your Good Health for more of my story.", "You closing the book and looking at the camera.", "Talk with your doctor first")],
  "Why I wrote The Fasting Blueprint. My own experience, not a typical result.", ["#booktok", "#myjourney", "#selfpublishedauthor", "#personalstory"]),
 (25, "gadgets", "discovery", "Portable heated lunch box",
  "Cold leftovers at lunch? Not today.", "Warm lunch anywhere", ["lunch box", "warm"],
  [("Cold leftovers at lunch? Not today.", "A cold container of leftovers on your desk or car seat.", "Warm lunch anywhere"),
   ("This is a portable heated lunch box.", "Hold the lunch box up and open it.", "Heated lunch box"),
   ("Food goes in, I plug it in, and I come back to a warm lunch.", "Put the food in, plug it in, then open it later with steam showing.", "Plug in, come back"),
   ("Follow 4your Good Health for more finds like this.", "You taking the first bite.", "Follow for more")],
  "A portable heated lunch box, shown from cold to warm. If you use my link, I earn a commission.", ["#productfinds", "#lunchbox", "#worklunch", "#gadgets"]),
 (26, "money", "quick_tip", "Shop your pantry before you shop the store",
  "I was buying things I already had.", "Shop the pantry", ["pantry", "list"],
  [("I was buying things I already had.", "Open the pantry and pull out two of the same item.", "Shop the pantry"),
   ("Now I check the pantry and write the list from what's missing.", "Write a short grocery list while looking at the shelves.", "List what's missing"),
   ("I plan two meals around what's already there.", "Set out pantry items for one meal on the counter.", "Two meals from here"),
   ("Save this before your next trip, and follow 4your Good Health for more money savers.", "The short list on the fridge.", "Save before you shop")],
  "Before the store: check the pantry, then write the list.", ["#moneysavingtips", "#groceryhaul", "#mealplanning", "#frugalliving"]),
 (27, "gadgets", "demo", "Phone tripod with a remote",
  "This is how I film these by myself.", "My filming setup", ["tripod", "remote"],
  [("This is how I film these by myself.", "You stepping in front of the phone on its tripod.", "My filming setup"),
   ("It's a phone tripod with a small remote.", "Show the tripod and the remote in your hand.", "Tripod and remote"),
   ("I set the shot, step back, and press record from here.", "Press the remote and show the phone starting to record.", "Record hands-free"),
   ("Follow 4your Good Health and I'll show you more of how I make these.", "Wide shot of the full setup.", "Follow for more")],
  "The simple setup I use to film by myself: phone tripod and remote.", ["#contentcreatortips", "#phonetripod", "#productfinds", "#behindthescenes"]),
 (28, "kitchen", "demo", "Open a tight jar lid with a rubber band",
  "No jar opener? Grab a rubber band.", "Tight lid, no gadget", ["rubber band", "lid"],
  [("No jar opener? Grab a rubber band.", "A tight jar and a wide rubber band on the counter.", "Tight lid, no gadget"),
   ("Wrap the rubber band around the edge of the lid.", "Stretch the band around the lid.", "Band around the lid"),
   ("Now your hand has something to grip. Twist.", "Twist the lid open in one take.", "Grip and twist"),
   ("Share this with someone who needs it, and follow 4your Good Health for more kitchen hacks.", "The open jar.", "Share this one")],
  "A wide rubber band around the lid gives you grip.", ["#kitchenhacks", "#lifehacks", "#homehacks", "#cookingtips"]),
 (29, "health", "demo", "A two-minute stretch break at the desk",
  "Two minutes away from the screen. Stretch with me at your desk.", "Stretch break", ["stretch", "desk"],
  [("Two minutes away from the screen. Stretch with me at your desk.", "You at your desk pushing the chair back.", "Stretch break"),
   ("Reach both arms up and hold.", "Reach both arms overhead while seated or standing.", "Reach up"),
   ("Roll your shoulders back slowly, then look left and right.", "Shoulder rolls, then slow head turns.", "Shoulders, then neck"),
   ("Go gently, and stop if anything hurts. Follow 4your Good Health for more simple habits.", "You sitting back down, relaxed.", "Go gently")],
  "My two-minute desk stretch break. Go gently and stop if anything hurts.", ["#deskstretch", "#healthyhabits", "#workfromhome", "#wellness"]),
 (30, "gadgets", "comparison", "My three most-used finds this month",
  "Thirty days of gadgets. These three stayed on my counter.", "My top three", ["three", "gadgets"],
  [("Thirty days of gadgets. These three stayed on my counter.", "Three gadgets from this month lined up on the counter.", "My top three"),
   ("Third and second place: the two I reach for most days.", "Pick up the third, then the second, and use each briefly.", "Number 3 and number 2"),
   ("First place is the one I'd buy again tomorrow.", "Hold up your first pick and use it once.", "Number 1"),
   ("Which should I test next? Tell me, and follow 4your Good Health for the next round.", "All three back in a row.", "What's next?")],
  "After 30 days of testing, the three finds I still use.", ["#productfinds", "#gadgets", "#kitchengadgets", "#honestreview"]),
]

_RISKY = re.compile(
    r"\b(?:cures?|heals?|detox\w*|miracle|clinically proven|doctor[- ]recommended|guaranteed?|"
    r"burns? (?:belly )?fat|melts? fat|boosts? (?:your )?immun\w*|prevents? (?:disease|cancer|diabetes)|"
    r"treats? [a-z ]+|lose \d+ (?:pounds|lbs)|go(?:es|ing)? viral|risk[- ]free)\b", re.I)
_BAIT = re.compile(
    r"\b(?:follow for follow|f4f|like for like|l4l|comment (?:yes|\W*[\"']?\w+[\"']? )to (?:get|win|receive)|"
    r"tag (?:\d+|three|two|five) friends|share to win|like if you|type (?:yes|amen)|giveaway)\b", re.I)
_INVITE = re.compile(r"\b(?:follow|comment|tell me|save|share)\b", re.I)
_LOSS = re.compile(r"\blost \d+ (?:pounds|lbs)\b", re.I)


def entry(day):
    for row in _PLAN:
        if row[0] == day:
            d, category, fmt, topic, hook, cover, keywords, scenes, caption, tags = row
            return {"day": d, "category": category, "category_name": CATEGORIES[category],
                    "format": fmt, "format_name": FORMATS[fmt][0], "format_how": FORMATS[fmt][1],
                    "topic": topic, "hook": hook, "cover_text": cover, "keywords": keywords,
                    "scenes": [{"say": s, "show": v, "text": t} for s, v, t in scenes],
                    "caption": caption, "hashtags": tags}
    raise KeyError(day)


def plan():
    return [entry(row[0]) for row in _PLAN]


def review(item, lines=None):
    """Plain-language checks on one day's script. Returns a list of results.

    Each result is {"check", "ok", "note"}. A script is ready when every check
    passes. ``lines`` are the owner's edited spoken lines, if any.
    """
    lines = [line.strip() for line in (lines or [scene["say"] for scene in item["scenes"]])]
    spoken = " ".join(lines)
    words = len(spoken.split())
    results = []

    def add(check, ok, note):
        results.append({"check": check, "ok": bool(ok), "note": note})

    add("Four complete scenes", len(lines) == 4 and all(lines),
        "The video needs four spoken lines, one per scene.")
    lowered = spoken.casefold()
    missing = [k for k in item["keywords"] if k.casefold() not in lowered]
    add("Script matches the topic", not missing,
        "Fine." if not missing else "The script never mentions: " + ", ".join(missing) + ".")
    add("Opening is the hook", bool(lines and lines[0]) and len(lines[0].split()) <= 14,
        "Keep the first line to 14 words or fewer so it lands in the first two seconds.")
    long_lines = [i + 1 for i, line in enumerate(lines) if len(line.split()) > 24]
    if long_lines:
        length_note = "Shorten scene " + ", ".join(map(str, long_lines)) + " to 24 words or fewer."
    elif words > 85:
        length_note = "Trim the script to about 85 words."
    else:
        length_note = "Fine."
    add("Lines are short enough to caption", not long_lines and words <= 85, length_note)
    long_text = [i + 1 for i, scene in enumerate(item["scenes"]) if len(scene["text"]) > 40]
    add("On-screen text is readable", not long_text,
        "Fine." if not long_text else "On-screen text is too long in scene " + ", ".join(map(str, long_text)) + ".")
    risky = _RISKY.search(spoken + " " + item["caption"])
    add("No unsupported health or results claims", not risky,
        "Fine." if not risky else f'Remove or reword "{risky.group(0)}".')
    loss_ok = not _LOSS.search(spoken) or "not a typical result" in lowered
    add("Personal results are labeled", loss_ok,
        "Fine." if loss_ok else 'Say "my own experience, not a typical result" with any weight you lost.')
    bait = _BAIT.search(spoken + " " + item["caption"])
    last = lines[-1] if lines else ""
    add("Ending invites, without bait", bool(_INVITE.search(last)) and not bait,
        "Fine." if _INVITE.search(last) and not bait else
        "End with a plain invitation to follow, comment, save or share. No giveaways or 'comment YES'.")
    add("Ending names your account", ACCOUNT.casefold() in last.casefold(),
        f'Say "{ACCOUNT}" in the last line.')
    tags = item["hashtags"]
    add("Caption and hashtags", 0 < len(item["caption"]) <= 150 and 3 <= len(tags) <= 5
        and all(re.fullmatch(r"#[a-z0-9]+", tag) for tag in tags),
        "Keep the caption under 150 characters with 3 to 5 hashtags.")
    return results


def ready(item, lines=None):
    return all(result["ok"] for result in review(item, lines))


def _average(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def advice(posts, followers_now=None, day=None):
    """Turn logged results into plain next steps. Never predicts or promises."""
    followers = followers_now if followers_now is not None else START_FOLLOWERS + sum(p.get("follower_change") or 0 for p in posts)
    remaining = max(0, GOAL_FOLLOWERS - followers)
    days_left = max(1, DAYS - (day or 1) + 1)
    notes = {"followers": followers, "remaining": remaining, "days_left": days_left,
             "pace_needed": -(-remaining // days_left), "repeat": [], "adjust": [], "summary": []}
    if remaining == 0:
        notes["summary"].append("You have reached 1,000 followers. Keep the formats that got you here.")
    else:
        notes["summary"].append(
            f"{remaining} more followers to reach {GOAL_FOLLOWERS:,}. That is about {notes['pace_needed']} a day "
            f"over the {days_left} days left. No plan can guarantee that number; use it only as a yardstick.")
    if len(posts) < 3:
        notes["summary"].append("Log at least three posts and this section will tell you what to repeat.")
        return notes

    def groups(field, names):
        table = {}
        for post in posts:
            table.setdefault(post.get(field), []).append(post)
        rows = []
        for key, items in table.items():
            if key in names and len(items) >= 1:
                rows.append({"key": key, "name": names[key] if isinstance(names[key], str) else names[key][0],
                             "posts": len(items), "views": _average(items, "views") or 0,
                             "follows": _average(items, "follower_change") or 0})
        return rows

    for field, names, label in (("category", CATEGORIES, "topic"), ("format", FORMATS, "format")):
        rows = groups(field, names)
        if len(rows) >= 2:
            best = max(rows, key=lambda r: (r["follows"], r["views"]))
            worst = min(rows, key=lambda r: (r["follows"], r["views"]))
            if best["key"] != worst["key"] and (best["follows"], best["views"]) > (worst["follows"], worst["views"]):
                notes["repeat"].append(
                    f"Best {label} so far: {best['name']} (about {best['follows']:.0f} new followers and "
                    f"{best['views']:.0f} views a post over {best['posts']} post(s)). Make more of these.")
                notes["adjust"].append(
                    f"Weakest {label} so far: {worst['name']} (about {worst['follows']:.0f} new followers and "
                    f"{worst['views']:.0f} views a post). Try a different hook or format before dropping it.")
    retention = _average(posts, "retention")
    watch = _average(posts, "watch_seconds")
    if retention is not None and retention < 30 or watch is not None and watch < 4:
        notes["adjust"].append("People are leaving early. Make the first two seconds show the result or the problem, with fewer words.")
    visits, views = _average(posts, "profile_visits"), _average(posts, "views")
    gained = _average(posts, "follower_change")
    if visits and gained is not None and visits >= 10 and gained / visits < 0.1:
        notes["adjust"].append("People visit your profile but few follow. Tidy your bio and pin your three best videos.")
    if views and (_average(posts, "saves") or 0) + (_average(posts, "shares") or 0) >= views * 0.02:
        notes["repeat"].append("Your videos are being saved and shared at a healthy rate. Keep the how-to style.")
    if not notes["repeat"] and not notes["adjust"]:
        notes["summary"].append("Results are close across your posts so far. Keep posting daily and log each one.")
    return notes
