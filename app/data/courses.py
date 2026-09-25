"""The three built-in short courses (loaded by ``flask seed-courses`` and on the first visit to the Courses page).

Each question has four answers and exactly one correct one (``correct`` is its index, 0-3).
Admins can change the questions and answers afterwards in Admin > Courses.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionSeed:
    text: str
    answers: tuple[str, str, str, str]
    correct: int
    explanation: str


@dataclass(frozen=True)
class CourseSeed:
    slug: str
    title: str
    summary: str
    icon: str
    minutes: int
    content: str
    questions: tuple[QuestionSeed, ...]


BUDGETING = CourseSeed(
    slug="budgeting-basics",
    title="Budgeting basics on R1 750",
    summary="Plan your NSFAS allowance before the month starts, so it lasts until the next payment.",
    icon="wallet2",
    minutes=10,
    content="""A budget is a plan for your money, made before you spend it. It tells every rand where to go, so the
allowance does not simply disappear in the first two weeks.

Start with what comes in. For most NSFAS students that is the R1 750 monthly allowance. Write it down as your
total and never plan to spend more than it.

Then list what must be paid: groceries, toiletries, transport and data. These are your needs. Wants, like takeaways
or new clothes, come only after the needs are covered.

A simple rule many students use is to split the money into needs, wants and savings:
- about 70% for needs (food, toiletries, transport)
- about 20% for wants
- about 10% saved for emergencies or next month

Track as you go. Every time you buy something, check it against your budget. In this app, your shopping list shows
what is already used and what is left, and it warns you before you go over.

At the end of the month, look back. Which category ran out first? Adjust next month's plan instead of giving up.
A budget is not a punishment: it is how you stay in control.""",
    questions=(
        QuestionSeed(
            "What is a budget?",
            (
                "A list of things you already bought",
                "A plan for your money made before you spend it",
                "A loan from the bank",
                "A way to earn more money",
            ),
            1,
            "A budget is made before spending: it gives every rand a job.",
        ),
        QuestionSeed(
            "What should you write down first when you make a budget?",
            ("Your wants", "Your total income for the month", "Last month's takeaways", "Your friends' spending"),
            1,
            "Start with what comes in, so you never plan to spend more than you have.",
        ),
        QuestionSeed(
            "Which of these is a need?",
            ("A new pair of sneakers", "Takeaway pizza", "Groceries for the week", "A music subscription"),
            2,
            "Needs keep you fed, clean and able to get to class. The rest are wants.",
        ),
        QuestionSeed(
            "In the 70/20/10 rule, what is the 10% for?",
            ("Takeaways", "Savings", "Data bundles", "Clothes"),
            1,
            "10% goes to savings: an emergency fund or a cushion for next month.",
        ),
        QuestionSeed(
            "Your allowance is R1 750. About how much is 70% for needs?",
            ("R525", "R875", "R1 225", "R1 575"),
            2,
            "70% of R1 750 is R1 225.",
        ),
        QuestionSeed(
            "When should you check your spending against your budget?",
            ("Only at the end of the year", "Every time you buy something", "Never", "Only when you run out"),
            1,
            "Tracking as you go stops small purchases from adding up unnoticed.",
        ),
        QuestionSeed(
            "Your grocery money ran out in week three. What is the best next step?",
            (
                "Stop budgeting, it does not work",
                "Borrow from a loan shark",
                "Look at what happened and adjust next month's plan",
                "Spend the savings on takeaways",
            ),
            2,
            "A budget improves every month: learn from what ran out first.",
        ),
        QuestionSeed(
            "Which should be paid for first?",
            ("Wants", "Needs", "Gifts for friends", "Entertainment"),
            1,
            "Cover needs first; wants come from what is left.",
        ),
        QuestionSeed(
            "What does this app do when your shopping list goes over your budget?",
            (
                "Deletes your list",
                "Warns you and asks you to replace items or reduce quantities",
                "Charges you a fee",
                "Nothing at all",
            ),
            1,
            "You can still add items, but you are warned and cannot proceed until the list fits the budget.",
        ),
        QuestionSeed(
            "Why is a budget NOT a punishment?",
            (
                "Because it lets you spend without limits",
                "Because it keeps you in control of your money",
                "Because the bank pays you for it",
                "Because you only do it once",
            ),
            1,
            "A budget gives you control, so the month does not end before the money does.",
        ),
    ),
)

SAVING = CourseSeed(
    slug="smart-saving",
    title="Smart saving for students",
    summary="Small, regular savings add up. Learn how to build an emergency fund on a student allowance.",
    icon="piggy-bank",
    minutes=8,
    content="""Saving means keeping part of your money for later instead of spending it now. Even on a small
allowance, small amounts saved regularly add up: R50 a month is R600 by the end of the year.

Pay yourself first. Move your savings amount aside on the day the allowance arrives, before you start spending.
Money that stays in your main account tends to get spent.

Build an emergency fund. This is money kept only for surprises: a broken phone screen, a trip home, medicine.
Having it means you do not need to borrow when something goes wrong.

Cut the small leaks. Airtime bought in small amounts, daily cold drinks and delivery fees quietly eat your money.
Buying data bundles monthly and cooking with friends saves a lot.

Beware of debt. Loan sharks and "buy now, pay later" offers charge high interest, so you pay back much more than
you borrowed. Saving first is always cheaper than borrowing.

Set a goal. "Save R300 for my trip home in December" is easier to stick to than "save some money". Check your
progress every month.""",
    questions=(
        QuestionSeed(
            "What does saving mean?",
            (
                "Spending everything as soon as it arrives",
                "Keeping part of your money for later",
                "Borrowing money from a friend",
                "Buying things on sale",
            ),
            1,
            "Saving is putting money aside now to use later.",
        ),
        QuestionSeed(
            "If you save R50 every month, how much do you have after 12 months?",
            ("R50", "R120", "R500", "R600"),
            3,
            "R50 x 12 months = R600.",
        ),
        QuestionSeed(
            "What does 'pay yourself first' mean?",
            (
                "Buy yourself a treat before paying for groceries",
                "Put your savings aside as soon as the money arrives",
                "Pay your friends back first",
                "Pay the loan shark first",
            ),
            1,
            "Moving savings aside on payday means they do not get spent by accident.",
        ),
        QuestionSeed(
            "What is an emergency fund for?",
            ("Weekend parties", "Unexpected costs like medicine or a trip home", "New clothes", "Daily takeaways"),
            1,
            "It is kept only for surprises, so you do not have to borrow.",
        ),
        QuestionSeed(
            "Which of these is a 'small leak' that wastes money?",
            (
                "Buying a monthly data bundle",
                "Cooking with friends",
                "Buying airtime in small amounts every day",
                "Using a shopping list",
            ),
            2,
            "Small daily purchases usually cost more than buying once a month.",
        ),
        QuestionSeed(
            "Why are loan sharks dangerous?",
            (
                "They charge very high interest, so you pay back much more",
                "They pay you interest",
                "They are the same as savings",
                "They give free money",
            ),
            0,
            "High interest means the debt grows quickly.",
        ),
        QuestionSeed(
            "Which savings goal is easiest to stick to?",
            (
                "Save some money one day",
                "Save R300 for my trip home in December",
                "Save whatever is left",
                "Save only when I feel like it",
            ),
            1,
            "A clear amount and date make a goal easy to follow and to check.",
        ),
        QuestionSeed(
            "What is usually cheaper?",
            ("Borrowing and paying interest", "Saving first and paying cash", "Buy now, pay later", "Paying late fees"),
            1,
            "Saving first means no interest and no fees.",
        ),
        QuestionSeed(
            "How often should you check your savings progress?",
            ("Every month", "Once every five years", "Never", "Only when you are broke"),
            0,
            "A monthly check keeps you on track.",
        ),
        QuestionSeed(
            "You budgeted R1 200 for groceries and spent R1 050. What happened?",
            ("You overspent by R150", "You saved R150 from your budget", "You spent R1 200", "You owe R1 050"),
            1,
            "Spending less than planned is a saving: this app shows it after each trip.",
        ),
    ),
)

SHOPPING = CourseSeed(
    slug="shop-smart",
    title="Shop smart: compare, plan and avoid traps",
    summary="Compare prices, plan one trip and avoid the tricks shops use to make you spend more.",
    icon="basket2",
    minutes=9,
    content="""The same product can cost very different amounts in different shops. Comparing before you go is one of
the easiest ways to make your allowance last longer.

Always shop with a list. Decide what you need at home, not in the shop. People who shop without a list buy more
than they planned.

Compare the price per unit, not only the price. A 2 kg bag of rice for R45 (R22.50 per kg) is cheaper than a 1 kg
bag for R26. Look at the small unit price on the shelf label.

Think about distance too. A product that is R1 cheaper but 6 km further away may cost you more in taxi fare and
time. This app shows the price and the distance together, so you can decide.

Plan one trip. Visiting the stores in a sensible order saves transport money. The shopping route in this app puts
the stores in the best order from your residence.

Watch for traps:
- items at eye level are often the most expensive; look higher and lower
- "2 for 1" is only a deal if you really need two
- sweets and snacks at the till are there for impulse buys

Store brands (house brands) are often much cheaper than big brands for basics like rice, maize meal, pasta and
cleaning products, and the quality is usually very similar.""",
    questions=(
        QuestionSeed(
            "Why should you compare prices before shopping?",
            (
                "Because all shops charge the same",
                "Because the same product can cost different amounts in different shops",
                "Because it is the law",
                "Because shops pay you to compare",
            ),
            1,
            "Prices differ between shops, so comparing saves money.",
        ),
        QuestionSeed(
            "When is the best time to decide what to buy?",
            ("While walking in the shop", "At home, before you go, with a list", "At the till", "After you pay"),
            1,
            "Deciding at home with a list prevents impulse buying.",
        ),
        QuestionSeed(
            "Rice: 1 kg for R26 or 2 kg for R45. Which is cheaper per kg?",
            ("The 1 kg bag", "The 2 kg bag", "They are the same", "You cannot tell"),
            1,
            "R45 / 2 kg = R22.50 per kg, less than R26 per kg.",
        ),
        QuestionSeed(
            "A product is R1 cheaper but 6 km further away. What should you think about?",
            (
                "Nothing, cheaper is always better",
                "The extra transport cost and time",
                "The colour of the store",
                "Which shop has more parking",
            ),
            1,
            "Taxi fare and time can cost more than the R1 you save.",
        ),
        QuestionSeed(
            "Why plan one shopping trip for several stores?",
            (
                "To spend more time in shops",
                "To save transport money and time",
                "Because shops close early",
                "To buy more snacks",
            ),
            1,
            "Visiting the stores in a sensible order saves money and time.",
        ),
        QuestionSeed(
            "Items at eye level on the shelf are often...",
            ("The cheapest", "Free", "The most expensive", "Out of date"),
            2,
            "Shops put pricier items where you look first. Look higher and lower.",
        ),
        QuestionSeed(
            "When is a '2 for 1' offer a real saving?",
            (
                "Always",
                "Only if you really need two",
                "Never",
                "Only on weekends",
            ),
            1,
            "If you do not need the second one, you spent money you did not plan to.",
        ),
        QuestionSeed(
            "Why are sweets placed at the till?",
            ("To encourage impulse buys", "Because they are healthy", "Because they are free", "To help you save"),
            0,
            "They tempt you while you wait. Stick to your list.",
        ),
        QuestionSeed(
            "Which is often a cheaper choice for basics like rice and pasta?",
            ("The biggest brand", "Store (house) brands", "Imported brands", "The smallest packets"),
            1,
            "Store brands cost less and the quality is usually similar.",
        ),
        QuestionSeed(
            "In this app, what does the Compare button show you?",
            (
                "Only the most expensive store",
                "Each store's price and how far it is from you",
                "Your exam results",
                "The weather",
            ),
            1,
            "Compare puts price and distance side by side, and flags a cheapest store that is far away.",
        ),
    ),
)

COURSES: tuple[CourseSeed, ...] = (BUDGETING, SAVING, SHOPPING)
