# -*- coding: utf-8 -*-
"""
:author: ludovic.delaune@oslandia.com
"""
from __future__ import unicode_literals
from base64 import encodestring
from datetime import datetime, timedelta
from passlib.hash import pbkdf2_sha256
from time import time

import boto.ses
from boto.s3.connection import S3Connection

from flask import current_app
from flask.ext.login import UserMixin
from sqlalchemy import Table, MetaData, Integer, String, Boolean, func, \
                       Float, Column, ForeignKey, DateTime, text, Index
from sqlalchemy.dialects.postgresql import JSONB, ENUM
from sqlalchemy import create_engine

from passlib.hash import pbkdf2_sha256

from itsdangerous import JSONWebSignatureSerializer

from prkng.processing.common import process_corrected_rules, process_corrections
from prkng.processing.filters import on_restriction
from prkng.utils import random_string

AUTH_PROVIDERS = (
    'facebook',
    'google',
    'email'
)

metadata = MetaData()


class db(object):
    """lazy loading of db"""
    engine = None


def init_model(app):
    """
    Initialize DB engine and create tables
    """
    if app.config['TESTING']:
        DATABASE_URI = 'postgresql://{user}:{password}@{host}:{port}/{database}'.format(
            user=app.config['PG_TEST_USERNAME'],
            password=app.config['PG_TEST_PASSWORD'],
            host=app.config['PG_TEST_HOST'],
            port=app.config['PG_TEST_PORT'],
            database=app.config['PG_TEST_DATABASE'],
        )
    else:
        DATABASE_URI = 'postgresql://{user}:{password}@{host}:{port}/{database}'.format(
            user=app.config['PG_USERNAME'],
            password=app.config['PG_PASSWORD'],
            host=app.config['PG_HOST'],
            port=app.config['PG_PORT'],
            database=app.config['PG_DATABASE'],
        )

    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI

    # lazy bind the sqlalchemy engine
    with app.app_context():
        db.engine = create_engine(
            '{SQLALCHEMY_DATABASE_URI}'.format(**app.config),
            strategy='threadlocal',
            pool_size=10
        )

    metadata.bind = db.engine
    # create model
    metadata.create_all()

user_table = Table(
    'users',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('name', String, nullable=False),
    Column('gender', String(10)),
    Column('email', String(60), index=True, unique=True, nullable=False),
    Column('created', DateTime, server_default=text('NOW()'), index=True),
    Column('apikey', String),
    Column('image_url', String)
)

# creating a functional index on apikey field
user_api_index = Index(
    'idx_users_apikey',
    func.substr(user_table.c.apikey, 0, 6)
)

checkin_table = Table(
    'checkins',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, ForeignKey("users.id"), index=True, nullable=False),
    Column('slot_id', Integer),
    Column('way_name', String),
    Column('long', Float),
    Column('lat', Float),
    Column('created', DateTime, server_default=text('NOW()'), index=True),
    # The time the check-in was created.
    Column('active', Boolean)
)

report_table = Table(
    'reports',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, ForeignKey("users.id"), index=True, nullable=False),
    Column('slot_id', Integer),
    Column('way_name', String),
    Column('long', Float),
    Column('lat', Float),
    Column('created', DateTime, server_default=text('NOW()'), index=True),
    Column('image_url', String),
    Column('notes', String),
    Column('progress', Integer, server_default="0")
)


userauth_table = Table(
    'users_auth',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, ForeignKey("users.id"), index=True, nullable=False),
    Column('auth_id', String(1024), index=True, unique=True),  # id given by oauth provider
    Column('auth_type', ENUM(*AUTH_PROVIDERS, name='auth_provider')),  # oauth_type
    Column('password', String),  # for the email accounts
    Column('fullprofile', JSONB),
    Column('reset_code', String, nullable=True)
)


class User(UserMixin):
    """
    Subclassed UserMixin for the methods that Flask-Login expects user objects to have
    """
    def __init__(self, kwargs):
        super(UserMixin, self).__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return u"<User {} : {}>".format(self.id, self.name)

    def update_apikey(self, newkey):
        """
        Update key in the database
        """
        db.engine.execute("""
            update users set apikey = '{key}'
            where id = {user_id}
            """.format(key=newkey, user_id=self.id))
        self.apikey = newkey

    def update_profile(self, name=None, email=None, gender=None, image_url=None):
        """
        Update profile information
        """
        db.engine.execute("""
            UPDATE users
            SET
                name = '{name}',
                email = '{email}',
                image_url = '{image_url}'
            WHERE id = {user_id}
            """.format(email=email or self.email,
                name=name or self.name,
                gender=gender or self.gender,
                image_url=image_url or self.image_url,
                user_id=self.id))
        self.name = name or self.name
        self.email = email or self.email
        self.gender = gender or self.gender
        self.image_url = image_url or self.image_url

    @property
    def json(self):
        vals = {
            key: value for key, value in self.__dict__.items()
        }
        # since datetime is not JSON serializable
        vals['created'] = self.created.strftime("%Y-%m-%dT%H:%M:%SZ")
        return vals

    @staticmethod
    def generate_apikey(email):
        """
        Generate a user API key
        """
        serial = JSONWebSignatureSerializer(current_app.config['SECRET_KEY'])
        return serial.dumps({
            'email': email,
            'time': time()
        })

    @staticmethod
    def get(id):
        """
        Static method to search the database and see if user with ``id`` exists.  If it
        does exist then return a User Object.  If not then return None as
        required by Flask-Login.
        """
        res = user_table.select(user_table.c.id == id).execute().first()
        if not res:
            return None
        return User(res)

    @staticmethod
    def get_byemail(email):
        """
        Static method to search the database and see if user with ``id`` exists.  If it
        does exist then return a User Object.  If not then return None as
        required by Flask-Login.
        """
        res = user_table.select(user_table.c.email == email.lower()).execute().first()
        if not res:
            return None
        return User(res)

    @staticmethod
    def get_byapikey(apikey):
        """
        Static method to search the database and see if user with ``apikey`` exists.  If it
        does exist then return a User Object.  If not then return None as
        required by Flask-Login.
        """
        res = db.engine.execute("""
            select * from users where
            substr(apikey::text, 0, 6) = substr('{0}', 0, 6)
            AND apikey = '{0}'
            """.format(apikey)).first()
        if not res:
            return None
        return User(res)

    @staticmethod
    def get_profile(id):
        """
        Static method to search the database and get a user profile.
        :returns: RowProxy object (ordereddict) or None if not exists
        """
        res = user_table.select(user_table.c.id == id).execute().first()
        if not res:
            return None
        return res

    @staticmethod
    def add_user(name=None, email=None, gender=None, image_url=None):
        """
        Add a new user.
        Raise an exception in case of already exists.
        """
        apikey = User.generate_apikey(email)
        # insert data
        db.engine.execute(user_table.insert().values(
            name=name, email=email, apikey=apikey, gender=gender, image_url=image_url))
        # retrieve new user informations
        res = user_table.select(user_table.c.email == email).execute().first()
        return User(res)


class UserAuth(object):
    """
    Represent an authentication method per user.
    On user can have several authentication methods (google + facebook for example).
    """
    @staticmethod
    def exists(auth_id):
        res = userauth_table.select(userauth_table.c.auth_id == auth_id).execute().first()
        return res

    @staticmethod
    def update(auth_id, birthyear):
        db.engine.execute(userauth_table.update().where(userauth_table.c.auth_id == auth_id).values(fullprofile={'birthyear': birthyear}))

    @staticmethod
    def update_password(auth_id, password, reset_code=None):
        if reset_code:
            u = userauth_table.select(userauth_table.c.auth_id == auth_id).execute().fetchone()
            if not u or reset_code != u["reset_code"]:
                return False
        crypt_pass = pbkdf2_sha256.encrypt(password, rounds=200, salt_size=16)
        db.engine.execute(userauth_table.update().where(userauth_table.c.auth_id == auth_id).values(password=crypt_pass, reset_code=None))
        return True

    @staticmethod
    def add_userauth(user_id=None, name=None, auth_id=None, auth_type=None,
                     email=None, fullprofile=None, password=None):
        db.engine.execute(userauth_table.insert().values(
            user_id=user_id,
            auth_id=auth_id,
            auth_type=auth_type,
            password=password,
            fullprofile=fullprofile
        ))

    @staticmethod
    def send_reset_code(auth_id, email):
        temp_passwd = random_string()[0:6]

        c = boto.ses.connect_to_region("us-west-2",
            aws_access_key_id=current_app.config["AWS_ACCESS_KEY"],
            aws_secret_access_key=current_app.config["AWS_SECRET_KEY"])
        c.send_email(
            "noreply@prk.ng",
            "prkng - Reset password",
            "Please visit the following address to change your password. If you did not request this password change, feel free to ignore this message. \n\nhttps://api.prk.ng/resetpassword?resetCode={}&email={}\n\nThanks for using prkng!".format(temp_passwd, email.replace('@', '%40')),
            email
        )

        db.engine.execute(userauth_table.update().where(userauth_table.c.auth_id == auth_id).values(reset_code=temp_passwd))


class Checkins(object):
    @staticmethod
    def get(user_id):
        """
        Get info on the user's current check-in
        """
        res = db.engine.execute("""
            SELECT id, slot_id, way_name, long, lat, created::text as created, active
            FROM checkins
            WHERE user_id = {}
            AND active = true
        """.format(user_id)).first()
        if not res:
            return None
        return dict(res)

    @staticmethod
    def get_all(user_id, limit):
        res = db.engine.execute("""
            SELECT id, slot_id, way_name, long, lat, created::text as created, active
            FROM checkins
            WHERE user_id = {uid}
            ORDER BY created DESC
            LIMIT {limit}
            """.format(uid=user_id, limit=limit)).fetchall()
        return [dict(row) for row in res]

    @staticmethod
    def add(user_id, slot_id):
        exists = db.engine.execute("""
            select 1 from slots where id = {slot_id}
            """.format(slot_id=slot_id)).first()
        if not exists:
            return False

        # if the user is already checked in elsewhere, deactivate their old checkin
        db.engine.execute(checkin_table.update().where(checkin_table.c.user_id == user_id).values(active=False))

        db.engine.execute("""
            INSERT INTO checkins (user_id, slot_id, way_name, long, lat, active)
            SELECT
                {user_id}, {slot_id}, way_name,
                (button_location->>'long')::float,
                (button_location->>'lat')::float,
                true
            FROM slots WHERE id = {slot_id}
        """.format(user_id=user_id, slot_id=slot_id))  # FIXME way_name
        return True

    @staticmethod
    def delete(user_id, checkin_id):
        db.engine.execute("""
            UPDATE checkins
            SET active = false
            WHERE user_id = {}
            AND id = {}
        """.format(user_id, checkin_id))
        return True


class SlotsModel(object):
    properties = (
        'id',
        'geojson',
        'rules',
        'button_location',
        'way_name'
    )

    @staticmethod
    def get_within(x, y, radius, duration, checkin=None, permit=False):
        """
        Retrieve the nearest slots within ``radius`` meters of a
        given location (x, y).

        Apply restrictions before sending the response
        """
        checkin = checkin or datetime.now()

        req = """
        SELECT 1 FROM service_areas
        WHERE ST_Intersects(geom, ST_Buffer(ST_Transform('SRID=4326;POINT({x} {y})'::geometry, 3857), 3))
        """.format(x=x, y=y)
        if not db.engine.execute(req).first():
            return False

        req = """
        SELECT {properties} FROM slots
        WHERE
            ST_Dwithin(
                st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857),
                geom,
                {radius}
            )
        """.format(
            properties=','.join(SlotsModel.properties),
            x=x,
            y=y,
            radius=radius
        )

        features = db.engine.execute(req).fetchall()

        return filter(
            lambda x: not on_restriction(x.rules, checkin, duration, permit),
            features
        )

    @staticmethod
    def get_boundbox(
            nelat, nelng, swlat, swlng, checkin=None, duration=0.25, type=None,
            permit=False, invert=False):
        """
        Retrieve all slots inside a given boundbox.
        """

        req = """
        SELECT 1 FROM service_areas
        WHERE ST_Intersects(geom, ST_Transform(ST_MakeEnvelope({nelng}, {nelat}, {swlng}, {swlat}, 4326), 3857))
        """.format(nelat=nelat, nelng=nelng, swlat=swlat, swlng=swlng)
        if not db.engine.execute(req).first():
            return False

        req = """
        SELECT {properties} FROM slots
        WHERE
            ST_intersects(
                ST_Transform(
                    ST_MakeEnvelope({nelng}, {nelat}, {swlng}, {swlat}, 4326),
                    3857
                ),
                slots.geom
            )
        """.format(
            properties=','.join(SlotsModel.properties),
            nelat=nelat,
            nelng=nelng,
            swlat=swlat,
            swlng=swlng
        )

        slots = db.engine.execute(req).fetchall()
        if checkin and invert:
            slots = filter(lambda x: on_restriction(x.rules, checkin, float(duration), permit), slots)
        elif checkin:
            slots = filter(lambda x: not on_restriction(x.rules, checkin, float(duration), permit), slots)
        if type == 1:
            slots = filter(lambda x: "paid" in [y["restrict_typ"] for y in x.rules], slots)
        elif type == 2:
            slots = filter(lambda x: "permit" in [y["restrict_typ"] for y in x.rules], slots)
        elif type == 3:
            slots = filter(lambda x: any([y["time_max_parking"] for y in x.rules]), slots)

        return slots

    @staticmethod
    def get_byid(sid):
        """
        Retrieve slot information by its ID
        """
        return db.engine.execute("""
            SELECT {properties}
            FROM slots
            WHERE id = {sid}
            """.format(sid=sid, properties=','.join(SlotsModel.properties))).fetchall()


class ServiceAreas(object):
    @staticmethod
    def get_all(returns="json"):
        return db.engine.execute("""
            SELECT
                gid AS id,
                name,
                name_disp,
                ST_As{}(ST_Transform(geom, 4326)) AS geom
            FROM service_areas
        """.format("GeoJSON" if returns == "json" else "KML")).fetchall()

    @staticmethod
    def get_mask(returns="json"):
        return db.engine.execute("""
            SELECT
                1,
                'world_mask',
                'world_mask',
                ST_As{}(ST_Transform(geom, 4326)) AS geom
            FROM service_areas_mask
        """.format("GeoJSON" if returns == "json" else "KML")).fetchall()


class ServiceAreasMeta(object):
    @staticmethod
    def get_all():
        res = db.engine.execute("""
            SELECT
                version,
                kml_addr,
                geojson_addr,
                kml_mask_addr,
                geojson_mask_addr
            FROM service_areas_meta
        """).fetchall()

        return [
            {key: value for key, value in row.items()}
            for row in res
        ]


# associate fields for each city provider
district_field = {
    'montreal': (
        'gid as id',
        'nom_qr as name',
        'ST_AsGeoJSON(st_transform(st_simplify(geom, 10), 4326)) as geom'
    ),
    'quebec': (
        'gid as id',
        'nom as name',
        'ST_AsGeoJSON(st_transform(st_simplify(geom, 10), 4326)) as geom'
    ),
}


class Images(object):
    @staticmethod
    def generate_s3_url(image_type, file_name):
        """
        Generate S3 submission URL valid for 24h, with which the user can upload an
        avatar or a report image.
        """
        file_name = random_string(16) + "." + file_name.rsplit(".")[1]

        c = S3Connection(current_app.config["AWS_ACCESS_KEY"],
            current_app.config["AWS_SECRET_KEY"])
        url = c.generate_url(86400, "PUT", current_app.config["AWS_S3_BUCKET"],
            image_type+"/"+file_name, headers={"x-amz-acl": "public-read",
                "Content-Type": "image/jpeg"})

        return {"request_url": url, "access_url": url.split("?")[0]}


class City(object):
    @staticmethod
    def get_checkins(city):
        res = db.engine.execute("""
            SELECT
                c.id,
                c.user_id,
                s.id AS slot_id,
                c.way_name,
                to_char(c.created, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created,
                u.name,
                u.email,
                u.gender,
                c.long,
                c.lat,
                c.active,
                a.auth_type AS user_type,
                s.rules
            FROM checkins c
            JOIN slots s ON s.id = c.slot_id
            JOIN users u ON c.user_id = u.id
            JOIN service_areas sa ON ST_intersects(s.geom, sa.geom)
            JOIN
                (SELECT auth_type, user_id, max(id) AS id
                    FROM users_auth GROUP BY auth_type, user_id) a
                ON c.user_id = a.user_id
            WHERE sa.name = '{}'
            """.format(city)).fetchall()

        return [
            {key: value for key, value in row.items()}
            for row in res
        ]

    @staticmethod
    def get_reports(city):
        res = db.engine.execute("""
            SELECT
                r.id,
                to_char(r.created, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created,
                r.slot_id,
                u.id AS user_id,
                u.name AS user_name,
                u.email AS user_email,
                s.way_name,
                s.rules,
                r.long,
                r.lat,
                r.image_url,
                r.notes,
                r.progress,
                ARRAY_REMOVE(ARRAY_AGG(c.id), NULL) AS corrections
            FROM reports r
            JOIN service_areas sa ON ST_intersects(ST_transform(ST_SetSRID(ST_MakePoint(r.long, r.lat), 4326), 3857), sa.geom)
            JOIN users u ON r.user_id = u.id
            LEFT JOIN slots s ON r.slot_id = s.id
            LEFT JOIN corrections c ON s.signposts = c.signposts
            WHERE sa.name = '{}'
            GROUP BY r.id, u.id, s.way_name, s.rules
            """.format(city)).fetchall()

        return [
            {key: value for key, value in row.items()}
            for row in res
        ]

    @staticmethod
    def get_corrections(city):
        res = db.engine.execute("""
            SELECT
                c.*,
                s.id AS slot_id,
                s.way_name,
                s.button_location ->> 'lat' AS lat,
                s.button_location ->> 'long' AS long,
                c.code = ANY(ARRAY_AGG(codes->>'code')) AS active
            FROM corrections c,
                slots s,
                jsonb_array_elements(s.rules) codes
            WHERE c.city = '{}'
                AND c.signposts = s.signposts
            GROUP BY c.id, s.id
        """.format(city)).fetchall()

        return [
            {key: value for key, value in row.items()}
            for row in res
        ]


class Reports(object):
    @staticmethod
    def add(user_id, slot_id, lng, lat, url, notes):
        db.engine.execute("""
            INSERT INTO reports (user_id, slot_id, way_name, long, lat, image_url, notes)
            SELECT {user_id}, {slot_id}, s.way_name, {lng}, {lat}, '{image_url}', '{notes}'
              FROM slots s
              WHERE s.id = {slot_id}
        """.format(user_id=user_id, slot_id=slot_id or "NULL", lng=lng, lat=lat,
            image_url=url, notes=notes))

    @staticmethod
    def get(id):
        res = db.engine.execute("""
            SELECT
                r.id,
                to_char(r.created, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created,
                r.slot_id,
                u.id AS user_id,
                u.name AS user_name,
                u.email AS user_email,
                s.way_name,
                s.rules,
                r.long,
                r.lat,
                r.image_url,
                r.notes,
                r.progress,
                ARRAY_REMOVE(ARRAY_AGG(c.id), NULL) AS corrections
            FROM reports r
            JOIN users u ON r.user_id = u.id
            LEFT JOIN slots s ON r.slot_id = s.id
            LEFT JOIN corrections c ON s.signposts = c.signposts
            WHERE r.id = {}
            GROUP BY r.id, u.id, s.way_name, s.rules
        """.format(id)).first()

        return {key: value for key, value in res.items()}

    @staticmethod
    def set_progress(id, progress):
        res = db.engine.execute("""
            UPDATE reports r
              SET progress = {}
              FROM users u, slots s
              WHERE r.id = {}
                AND r.user_id = u.id
                AND r.slot_id = s.id
            RETURNING r.*, u.name, u.email, s.way_name
        """.format(progress, id)).first()

        return {key: value for key, value in res.items()}

    @staticmethod
    def delete(id):
        db.engine.execute(report_table.delete().where(report_table.c.id == id))


class Corrections(object):
    @staticmethod
    def add(
            slot_id, code, city, description, initials, season_start, season_end,
            time_max_parking, agenda, special_days, restrict_typ):
        # get signposts by slot ID
        res = db.engine.execute("""
            SELECT signposts FROM slots WHERE id = {}
        """.format(slot_id)).first()
        if not res:
            return False
        signposts = res[0]

        # map correction to signposts and save
        res = db.engine.execute(
            """
            INSERT INTO corrections
                (initials, signposts, code, city, description, season_start, season_end,
                    time_max_parking, agenda, special_days, restrict_typ)
            SELECT '{initials}', ARRAY{signposts}, '{code}', '{city}', '{description}',
                '{season_start}', '{season_end}', {time_max_parking}, '{agenda}'::jsonb,
                '{special_days}', '{restrict_typ}'
            RETURNING *
            """.format(initials=initials, signposts=signposts, code=code, city=city,
                description=description, season_start=season_start,
                season_end=season_end, time_max_parking=time_max_parking,
                agenda=agenda, special_days=special_days, restrict_typ=restrict_typ)
        ).first()
        return {key: value for key, value in res.items()}

    @staticmethod
    def apply():
        # apply any pending corrections to existing slots
        db.engine.execute(text(process_corrected_rules).execution_options(autocommit=True))
        db.engine.execute(text(process_corrections).execution_options(autocommit=True))

    @staticmethod
    def get(id):
        res = db.engine.execute("""
            SELECT
                c.*,
                s.id AS slot_id,
                s.way_name,
                s.button_location ->> 'lat' AS lat,
                s.button_location ->> 'long' AS long,
                c.code = ANY(ARRAY_AGG(codes->>'code')) AS active
            FROM corrections c,
                slots s,
                jsonb_array_elements(s.rules) codes
            WHERE c.id = {}
              AND s.signposts = c.signposts
            GROUP BY c.id, s.id
        """.format(id)).first()
        if not res:
            return False

        return {key: value for key, value in res.items()}

    @staticmethod
    def delete(id):
        db.engine.execute("""
            DELETE FROM corrections
            WHERE id = {}
        """.format(id))


class Car2Go(object):
    @staticmethod
    def get(name):
        """
        Get a car2go by its name.
        """
        res = db.engine.execute("SELECT * FROM car2go WHERE name = {}".format(name)).first()
        return {key: value for key, value in res.items()}

    @staticmethod
    def get_all():
        """
        Get all active car2go records.
        """
        res = db.engine.execute("""
            SELECT
                c.id,
                c.name,
                c.vin,
                c.address,
                to_char(c.since, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS since,
                c.long,
                c.lat,
                s.rules
            FROM car2go c
            JOIN slots s ON c.slot_id = s.id
            WHERE c.in_lot = false
                AND c.parked = true
        """).fetchall()
        return [
            {key: value for key, value in row.items()}
            for row in res
        ]

    @staticmethod
    def get_free_spaces(minutes=5):
        """
        Get slots with car2gos that have recently left
        """
        start = datetime.now()
        finish = start - timedelta(minutes=int(minutes))
        res = db.engine.execute("""
            SELECT
                s.id,
                s.way_name,
                s.geojson,
                s.rules,
                s.button_location->>'lat' AS lat,
                s.button_location->>'long' AS long,
                to_char(c.since, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS since
            FROM slots s
            JOIN car2go c ON c.slot_id = s.id
            WHERE c.in_lot   = false
                AND c.parked = false
                AND c.since  > '{}'
                AND c.since  < '{}'
        """.format(finish.strftime('%Y-%m-%d %H:%M:%S'), start.strftime('%Y-%m-%d %H:%M:%S')))
        return [
            {key: value for key, value in row.items()}
            for row in res
        ]


class Analytics(object):
    @staticmethod
    def get_user_data():
        today = db.engine.execute("""
            SELECT count(id)
            FROM users
            WHERE created >= (NOW() AT TIME ZONE 'US/Eastern')::date
              AND created <= (NOW() AT TIME ZONE 'US/Eastern' + INTERVAL '1 DAY')::date
        """).first()[0]
        week = db.engine.execute("""
            SELECT
              a.date, count(u.id)
            FROM (
              SELECT
                to_char(date_trunc('day', ((NOW() AT TIME ZONE 'US/Eastern')::date - (offs * INTERVAL '1 DAY'))), 'YYYY-MM-DD"T"HH24:MI:SS"-0400"') AS date
              FROM generate_series(0, 365, 1) offs
            ) a
            LEFT OUTER JOIN users u
              ON (a.date = to_char(date_trunc('day', (u.created AT TIME ZONE 'UTC') AT TIME ZONE 'US/Eastern'), 'YYYY-MM-DD"T"HH24:MI:SS"-0400"'))
            GROUP BY a.date
            ORDER BY a.date DESC
            OFFSET 1 LIMIT 6
        """)
        return {"day": today, "week": [{key: value for key, value in row.items()} for row in week]}

    @staticmethod
    def get_active_user_data():
        today = db.engine.execute("""
            SELECT count(DISTINCT u.id)
            FROM users u
            JOIN checkins c ON u.id = c.user_id
            WHERE c.created >= (NOW() AT TIME ZONE 'US/Eastern')::date
              AND c.created <= (NOW() AT TIME ZONE 'US/Eastern' + INTERVAL '1 DAY')::date
        """).first()[0]
        week = db.engine.execute("""
            SELECT
              a.date, count(DISTINCT c.user_id)
            FROM (
              SELECT
                to_char(date_trunc('day', ((NOW() AT TIME ZONE 'US/Eastern')::date - (offs * INTERVAL '1 DAY'))), 'YYYY-MM-DD"T"HH24:MI:SS"-0400"') AS date
              FROM generate_series(0, 365, 1) offs
            ) a
            LEFT OUTER JOIN checkins c
              ON (a.date = to_char(date_trunc('day', (c.created AT TIME ZONE 'UTC') AT TIME ZONE 'US/Eastern'), 'YYYY-MM-DD"T"HH24:MI:SS"-0400"'))
            GROUP BY a.date
            ORDER BY a.date DESC
            OFFSET 1 LIMIT 6
        """)
        return {"day": today, "week": [{key: value for key, value in row.items()} for row in week]}

    @staticmethod
    def get_checkin_data():
        today = db.engine.execute("""
            SELECT count(id)
            FROM checkins
            WHERE created >= (NOW() AT TIME ZONE 'US/Eastern')::date
              AND created <= (NOW() AT TIME ZONE 'US/Eastern' + INTERVAL '1 DAY')::date
        """).first()[0]
        week = db.engine.execute("""
            SELECT
              a.date, count(c.id)
            FROM (
              SELECT
                to_char(date_trunc('day', ((NOW() AT TIME ZONE 'US/Eastern')::date - (offs * INTERVAL '1 DAY'))), 'YYYY-MM-DD"T"HH24:MI:SS"-0400"') AS date
              FROM generate_series(0, 365, 1) offs
            ) a
            LEFT OUTER JOIN checkins c
              ON (a.date = to_char(date_trunc('day', (c.created AT TIME ZONE 'UTC') AT TIME ZONE 'US/Eastern'), 'YYYY-MM-DD"T"HH24:MI:SS"-0400"'))
            GROUP BY a.date
            ORDER BY a.date DESC
            OFFSET 1 LIMIT 6
        """)
        return {"day": today, "week": [{key: value for key, value in row.items()} for row in week]}
