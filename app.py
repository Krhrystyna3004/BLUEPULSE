import os, re, hashlib
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-before-deploying")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///bluepulse.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    groups = db.relationship("Group", secondary=membership if "membership" in globals() else "membership", backref="members")
    subscriptions = db.relationship("Topic", secondary="subscription", viewonly=True)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Topic(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(90), unique=True, nullable=False)
    description = db.Column(db.String(240), default="")

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), default="")
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"))
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    is_public = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    topic = db.relationship("Topic")
    owner = db.relationship("User")

membership = db.Table("membership",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("group_id", db.Integer, db.ForeignKey("group.id"), primary_key=True))

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    source_name = db.Column(db.String(120), default="Редакція BluePulse")
    source_url = db.Column(db.String(700), default="")
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"))
    group_id = db.Column(db.Integer, db.ForeignKey("group.id"))
    published_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_featured = db.Column(db.Boolean, default=False)
    topic = db.relationship("Topic")
    group = db.relationship("Group")

class Subscription(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), primary_key=True)

def current_user():
    return db.session.get(User, session.get("user_id")) if session.get("user_id") else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Увійдіть, щоб скористатися цією функцією.", "info")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_globals():
    return {"me": current_user(), "nav_topics": Topic.query.order_by(Topic.name).all()}

@app.route("/")
def home():
    topic = request.args.get("topic", type=int)
    group = request.args.get("group", type=int)
    q = request.args.get("q", "").strip()
    query = Article.query
    if topic: query = query.filter_by(topic_id=topic)
    if group: query = query.filter_by(group_id=group)
    if q: query = query.filter(or_(Article.title.ilike(f"%{q}%"), Article.summary.ilike(f"%{q}%"), Article.source_name.ilike(f"%{q}%")))
    articles = query.order_by(Article.is_featured.desc(), Article.published_at.desc()).all()
    return render_template("home.html", articles=articles, topics=Topic.query.all(),
                           groups=Group.query.filter_by(is_public=True).all(), selected_topic=topic,
                           selected_group=group, q=q)

@app.route("/article/<int:article_id>")
def article(article_id):
    item = db.session.get(Article, article_id) or abort(404)
    return render_template("article.html", article=item)

@app.route("/topics")
def topics():
    return render_template("topics.html", topics=Topic.query.all())

@app.route("/topic/<int:topic_id>")
def topic_page(topic_id):
    t = db.session.get(Topic, topic_id) or abort(404)
    articles = Article.query.filter_by(topic_id=t.id).order_by(Article.published_at.desc()).all()
    groups = Group.query.filter_by(topic_id=t.id, is_public=True).all()
    return render_template("topic.html", topic=t, articles=articles, groups=groups)

@app.route("/groups")
def groups():
    return render_template("groups.html", groups=Group.query.filter_by(is_public=True).order_by(Group.created_at.desc()).all())

@app.route("/group/<int:group_id>")
def group_page(group_id):
    g = db.session.get(Group, group_id) or abort(404)
    if not g.is_public and (not current_user() or current_user().id != g.owner_id): abort(404)
    joined = bool(current_user() and g in current_user().groups)
    return render_template("group.html", group=g, joined=joined,
        articles=Article.query.filter_by(group_id=g.id).order_by(Article.published_at.desc()).all())

@app.route("/groups/create", methods=["GET", "POST"])
@login_required
def create_group():
    if request.method == "POST":
        name, desc = request.form.get("name","").strip(), request.form.get("description","").strip()
        if not name: flash("Вкажіть назву групи.", "error")
        else:
            g = Group(name=name, description=desc, topic_id=request.form.get("topic_id", type=int), owner_id=current_user().id,
                      is_public=request.form.get("is_public") == "on")
            db.session.add(g); db.session.commit()
            current_user().groups.append(g); db.session.commit()
            flash("Групу створено!", "success"); return redirect(url_for("group_page", group_id=g.id))
    return render_template("create_group.html", topics=Topic.query.all())

@app.route("/group/<int:group_id>/join", methods=["POST"])
@login_required
def join_group(group_id):
    g = db.session.get(Group, group_id) or abort(404)
    if g not in current_user().groups: current_user().groups.append(g); db.session.commit()
    flash("Ви приєдналися до групи.", "success")
    return redirect(url_for("group_page", group_id=g.id))

@app.route("/group/<int:group_id>/leave", methods=["POST"])
@login_required
def leave_group(group_id):
    g = db.session.get(Group, group_id) or abort(404)
    if g in current_user().groups: current_user().groups.remove(g); db.session.commit()
    flash("Ви залишили групу.", "info")
    return redirect(url_for("group_page", group_id=g.id))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username, email, password = (request.form.get(x,"").strip() for x in ("username","email","password"))
        if len(username) < 3 or len(password) < 8:
            flash("Ім’я має містити від 3 символів, пароль — від 8.", "error")
        elif User.query.filter(or_(User.username==username, User.email==email)).first():
            flash("Користувач із таким ім’ям або email вже існує.", "error")
        else:
            u=User(username=username,email=email); u.set_password(password); db.session.add(u); db.session.commit()
            session["user_id"]=u.id; flash("Ласкаво просимо до BluePulse!", "success")
            return redirect(url_for("home"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identity=request.form.get("identity","").strip()
        u=User.query.filter(or_(User.username==identity, User.email==identity)).first()
        if u and u.check_password(request.form.get("password","")):
            session.clear(); session["user_id"]=u.id
            return redirect(request.args.get("next") or url_for("home"))
        flash("Неправильний логін або пароль.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); flash("Ви вийшли з акаунта.", "info"); return redirect(url_for("home"))

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u=current_user()
    if request.method=="POST":
        u.bio=request.form.get("bio","")[:300]
        db.session.commit(); flash("Профіль оновлено.", "success")
    return render_template("profile.html", user=u, subscriptions=Topic.query.join(Subscription).filter(Subscription.user_id==u.id).all())

@app.route("/topic/<int:topic_id>/subscribe", methods=["POST"])
@login_required
def subscribe(topic_id):
    t=db.session.get(Topic,topic_id) or abort(404)
    existing=Subscription.query.filter_by(user_id=current_user().id,topic_id=t.id).first()
    if existing: db.session.delete(existing)
    else: db.session.add(Subscription(user_id=current_user().id,topic_id=t.id))
    db.session.commit()
    return redirect(request.referrer or url_for("topics"))

@app.route("/my-feed")
@login_required
def my_feed():
    ids=[s.topic_id for s in Subscription.query.filter_by(user_id=current_user().id).all()]
    articles=Article.query.filter(Article.topic_id.in_(ids)).order_by(Article.published_at.desc()).all() if ids else []
    return render_template("my_feed.html", articles=articles)

@app.route("/about")
def about(): return render_template("about.html")

def seed():
    if Topic.query.count(): return
    data=[("Технології","technology","Наука, цифрові продукти та інновації"),
          ("Суспільство","society","Події, люди та важливі зміни"),
          ("Освіта","education","Навчання, дослідження та можливості"),
          ("Культура","culture","Мистецтво, книги, кіно та події"),
          ("Екологія","ecology","Довкілля та сталі рішення"),
          ("Бізнес","business","Підприємництво та економіка")]
    topics=[]
    for name,slug,desc in data:
        t=Topic(name=name,slug=slug,description=desc); db.session.add(t); topics.append(t)
    db.session.commit()
    samples=[
      ("Як цифрові навички змінюють сучасний ринок праці","Аналітичний огляд відкритих освітніх програм і навичок, які допомагають адаптуватися до змін. Матеріал підготовлено редакцією на основі відкритих даних.","Освітня редакція",2),
      ("Малі екологічні звички з великим ефектом","Практичні кроки для зменшення побутових відходів: повторне використання речей, сортування та уважний вибір товарів.","Редакція BluePulse",4),
      ("Відкриті дані як інструмент прозорості","Публічні набори даних допомагають громадам відстежувати рішення та створювати корисні цифрові сервіси.","Редакція BluePulse",0),
      ("Культурні ініціативи, що об’єднують громади","Добірка ідей для локальних подій, читацьких клубів і творчих просторів.","Культурна редакція",3)]
    for title,summary,source,idx in samples:
        db.session.add(Article(title=title,summary=summary,source_name=source,topic_id=topics[idx].id,is_featured=(idx==0)))
    db.session.commit()

def ingest_feeds():
    """Import metadata + short summaries from explicitly configured RSS sources.
    Do not copy full articles. Verify each feed's license/terms before enabling it.
    """
    try: import feedparser
    except ImportError: app.logger.warning("feedparser is not installed; skipping RSS ingestion"); return 0
    from urllib.parse import urlparse
    urls=[x.strip() for x in os.getenv("RSS_FEEDS","").split(",") if x.strip()]
    allowed={x.strip().lower() for x in os.getenv("ALLOWED_FEED_HOSTS","").split(",") if x.strip()}
    added=0
    for feed_url in urls:
        host=urlparse(feed_url).hostname or ""
        if not host or (allowed and host.lower() not in allowed):
            app.logger.warning("Feed host not allowlisted: %s", host); continue
        parsed=feedparser.parse(feed_url)
        source=(parsed.feed.get("title") or host)[:120]
        for entry in parsed.entries[:20]:
            link=entry.get("link","")
            if not link or Article.query.filter_by(source_url=link).first(): continue
            title=re.sub(r"<[^>]+>","",entry.get("title","")).strip()[:240]
            # Keep a brief feed-provided description only; never mirror full article text.
            raw=entry.get("summary","")
            summary=re.sub(r"<[^>]+>"," ",raw)
            summary=re.sub(r"\s+"," ",summary).strip()[:650]
            if not title or not summary: continue
            # Conservative source handling: store a short factual description and link to origin.
            db.session.add(Article(title=title,summary=summary,source_name=source,source_url=link))
            added+=1
    db.session.commit()
    return added

def start_scheduler():
    if os.getenv("ENABLE_SCHEDULER","0") != "1": return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler=BackgroundScheduler(daemon=True, timezone=os.getenv("APP_TIMEZONE","Europe/Kyiv"))
        def daily_import():
            with app.app_context():
                ingest_feeds()
        scheduler.add_job(daily_import, "cron", hour=7, minute=0)
        scheduler.start()
    except Exception as exc: app.logger.exception("Scheduler could not start: %s", exc)

with app.app_context():
    db.create_all(); seed()
start_scheduler()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG","0")=="1")
