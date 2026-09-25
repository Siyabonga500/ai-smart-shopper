"""The public home page, About us / Contact us, the footer, and the route map on the shopping list."""

import re
from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, ContactMessage
from app.services import budgets, shopping
from app.services.retail_api import get_retail_provider

D = Decimal


def text(response):
    return " ".join(re.sub(r"<[^>]+>", " ", response.get_data(as_text=True)).replace(" ", " ").split())


def test_home_page_for_visitors_shows_products_and_the_top_bar(client):
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "About us" in html and 'href="/login"' in html and "Create account" in html  # the top bar
    assert 'id="products"' in html and html.count("Add to list") >= 8
    assert 'placeholder="Search' not in html and 'type="search"' not in html  # no search box on the home page
    add = re.search(r'href="(/login\?next=[^"]+)"[^>]*aria-label="Add ', html).group(1)
    assert add.startswith("/login?next=/search")  # after signing in the visitor lands on search for that product
    for category in ("Grocery", "Toiletries", "Clothing"):
        assert category in html
    assert "Electronics" not in html


def test_add_to_list_sends_a_visitor_to_sign_in_and_then_back(client, make_password_user):
    make_password_user(email="thandi@dut4life.ac.za")
    html = client.get("/").get_data(as_text=True)
    add = re.search(r'href="(/login\?next=[^"]+)"[^>]*aria-label="Add ', html).group(1).replace("&amp;", "&")
    assert client.get(add).status_code == 200
    response = client.post(add, data={"Email": "thandi@dut4life.ac.za", "Password": "Str0ng!Pass"})
    assert response.status_code == 302 and response.headers["Location"].startswith("/search?q=")


def test_signed_in_home_is_still_the_dashboard(client, make_user, login):
    login(make_user(first="Thabo"))
    assert b"Welcome, Thabo" in client.get("/").data


def test_every_page_has_the_footer(client, make_user, login):
    for path in ("/", "/about", "/login", "/register", "/terms"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="site-footer"' in html and "Tip of the day" in html, path
    login(make_user())
    assert 'class="site-footer"' in client.get("/budget").get_data(as_text=True)


def test_about_page_has_contact_details_and_a_form(client, app):
    app.config.update(CONTACT_EMAIL="help@example.org", CONTACT_PHONE="031 000 0000")
    page = text(client.get("/about"))
    assert "About us" in page and "Contact us" in page and "help@example.org" in page and "031 000 0000" in page


def test_contact_form_saves_the_message_for_the_admins(client, make_user, login):
    response = client.post(
        "/about",
        data={
            "Name": "Lindi",
            "Email": "lindi@dut4life.ac.za",
            "Subject": "Missing store",
            "Message": "Please add SPAR Berea.",
        },
    )
    assert response.status_code == 302
    message = db.session.scalar(select(ContactMessage))
    assert (message.Name, message.Subject, message.IsRead) == ("Lindi", "Missing store", False)

    bad = client.post("/about", data={"Name": "", "Email": "x", "Subject": "", "Message": "short"})
    assert bad.status_code == 400 and db.session.query(ContactMessage).count() == 1

    trap = client.post(
        "/about",
        data={
            "Name": "Bot",
            "Email": "bot@example.com",
            "Subject": "Buy",
            "Message": "cheap pills here",
            "Website": "x",
        },
    )
    assert trap.status_code == 302 and db.session.query(ContactMessage).count() == 1  # quietly dropped

    login(make_user(email="boss@example.com", IsAdmin=True))
    page = text(client.get("/admin/messages"))
    assert "Missing store" in page and "1 unread" in page
    client.post(f"/admin/messages/{message.MessageId}/read")
    assert db.session.get(ContactMessage, message.MessageId).IsRead is True
    client.post(f"/admin/messages/{message.MessageId}/delete")
    assert db.session.query(ContactMessage).count() == 0
    assert db.session.scalar(select(AuditLog).where(AuditLog.Action == "message.delete")) is not None


def test_sign_in_page_has_no_microsoft_button(client):
    html = client.get("/login").get_data(as_text=True)
    assert "Microsoft" not in html


def test_shopping_list_shows_the_map_of_stores_to_travel_to(client, make_user, login):
    user = make_user(Latitude=-29.8587, Longitude=31.0218)
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("500"))])
    offer = next(
        o for o in get_retail_provider().get_offers_by_barcode("2000000000275", -29.8587, 31.0218, 15) if o.in_stock
    )
    shopping.add_product(user, offer)
    login(user)
    html = client.get("/list").get_data(as_text=True)
    assert 'id="route-map"' in html and "Stores to travel to" in html and "route.js" in html and "leaflet" in html
    assert client.get("/api/route").get_json()["stops"]
