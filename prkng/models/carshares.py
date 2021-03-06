import datetime

from prkng.database import db, metadata

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Table, text
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry


carshares_table = Table(
    'carshares',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('city', String),
    Column('slot_id', Integer),
    Column('lot_id', Integer),
    Column('company', String),
    Column('vin', String, unique=True, nullable=True),
    Column('partner_id', String),
    Column('name', String),
    Column('geom', Geometry('POINT', 3857)),
    Column('address', String),
    Column('fuel', Integer),
    Column('since', DateTime, server_default=text('NOW()')),
    Column('until', DateTime, nullable=True),
    Column('parked', Boolean),
    Column('electric', Boolean, default=False),
    Column('geojson', JSONB)
)

carshare_lots_table = Table(
    'carshare_lots',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('partner_id', String),
    Column('city', String),
    Column('company', String),
    Column('name', String),
    Column('geom', Geometry('POINT', 3857)),
    Column('capacity', Integer),
    Column('available', Integer),
    Column('geojson', JSONB)
)


class Carshares(object):
    """
    This class handles the representation of carshares and carshare parking lots across different service areas.

    A carshare is a vehicle that can be shared between different users that subscribe to the same service, such as Car2Go, Communauto, or Zipcar. These services all have different characteristics but are served through our database as identical `carshare` objects that can be reserved through the accompanying company's API.

    Certain cities also have special reserved lots for a specific company's carshare vehicles, these can be retrieved from this object as well.
    """

    properties = (
        'id',
        'geojson',
        'vin',
        'company',
        'name',
        'fuel',
        'electric',
        'partner_id',
        'until'
    )
    select_properties = (
        'id',
        'geojson',
        'vin',
        'company',
        'name',
        'fuel',
        'electric',
        'partner_id',
        'until',
        'quantity'
    )
    lot_properties = (
        'id',
        'geojson',
        'company',
        'name',
        'capacity',
        'available'
    )

    @staticmethod
    def get(company, name):
        """
        Retrieve a specific carshare.

        :param company: name of the carshare company (str)
        :param name: vehicle `name` field -- usually maps to the car's license plate number (str)
        :returns: Carshare object (dict)
        """
        res = db.engine.execute("""
            SELECT * FROM carshares WHERE company = '{}' AND name = '{}' LIMIT 1
        """.format(company, name)).first()
        return {key: value for key, value in res.items()}

    @staticmethod
    def get_within(city, x, y, radius, company=False):
        """
        Get all parked carshares in a city within a particular radius.

        :param city: city name that is being searched in (str)
        :param x: longitude (int)
        :param y: latitude (int)
        :param radius: radius in meters to search in (int)
        :param company: filter by carshare company name (str), or False to get all
        :returns: list of Carshare objects (dicts)
        """
        qry = """
            SELECT {properties}, 1 AS quantity FROM carshares c
            WHERE c.city = '{city}' AND c.parked = true AND
                ST_Dwithin(
                    st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857),
                    c.geom,
                    {radius}
                )
        """
        if company and "," in company:
            qry += "AND c.company = ANY(ARRAY[{}])".format(",".join(["'"+z+"'" for z in company.split(",") if z != "zipcar"]))
        elif company and company != "zipcar":
            qry += "AND c.company = '{}'".format(company)
        if company and "zipcar" in company:
            qry += """
                UNION ALL
                SELECT DISTINCT ON (c.lot_id) {properties}, l.capacity AS quantity FROM carshares c
                JOIN carshare_lots l ON c.lot_id = l.id
                WHERE c.city = '{city}' AND c.parked = true AND
                    ST_Dwithin(
                        st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857),
                        c.geom,
                        {radius}
                    )
                AND c.company = 'zipcar'
            """
        res = db.engine.execute(qry.format(properties=', '.join(["c."+z for z in Carshares.properties]),
            city=city, x=x, y=y, radius=radius)).fetchall()
        data = []
        for x in res:
            x = list(x)
            for y in enumerate(x):
                if type(y[1]) == datetime.datetime:
                    x[y[0]] = y[1].isoformat()
            data.append(x)
        return data

    @staticmethod
    def get_nearest(city, x, y, limit, company=False):
        """
        Get nearest parked carshares in a city to a certain lat/long.

        :param city: city name that is being searched in (str)
        :param x: longitude (int)
        :param y: latitude (int)
        :param limit: number of nearest carshares to retrieve (int)
        :param company: filter by carshare company name (str), or False to get all
        :returns: list of Carshare objects (dicts)
        """
        qry = """
          WITH tmp AS (
            (SELECT {properties}, 1 AS quantity FROM carshares c
            WHERE c.city = '{city}' AND c.parked = true
        """
        if company and "," in company:
            qry += "AND c.company = ANY(ARRAY[{}])".format(",".join(["'"+z+"'" for z in company.split(",") if z != "zipcar"]))
        elif company and company != "zipcar":
            qry += "AND c.company = '{}'".format(company)
        qry += " ORDER BY ST_Distance(c.geom, st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857)))"
        if company and "zipcar" in company:
            qry += """
                UNION ALL
                (SELECT DISTINCT ON (c.lot_id) {properties}, l.capacity AS quantity FROM carshares c
                JOIN carshare_lots l ON c.lot_id = l.id
                WHERE c.city = '{city}' AND c.parked = true
                    AND c.company = 'zipcar'
                ORDER BY c.lot_id, ST_Distance(c.geom, st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857)))
            """
        qry += """
            )
            SELECT * FROM tmp
            LIMIT {limit}
        """
        res = db.engine.execute(qry.format(properties=', '.join(["c."+z for z in Carshares.properties]),
            city=city, x=x, y=y, limit=limit)).fetchall()
        data = []
        for x in res:
            x = list(x)
            for y in enumerate(x):
                if type(y[1]) == datetime.datetime:
                    x[y[0]] = y[1].isoformat()
            data.append(x)
        return data

    @staticmethod
    def get_boundbox(nelat, nelng, swlat, swlng):
        """
        Retrieve all parked carshares inside a given boundbox.

        :param nelat: latitude of northeast corner (int)
        :param nelng: longitude of northeast corner (int)
        :param swlat: latitude of southwest corner (int)
        :param swlng: longitude of southwest corner (int)
        :returns: list of Carshare objects (dicts)
        """

        res = db.engine.execute("""
            SELECT name FROM cities
            WHERE ST_Intersects(geom,
                ST_Transform(ST_MakeEnvelope({nelng}, {nelat}, {swlng}, {swlat}, 4326), 3857)
            )
        """.format(nelat=nelat, nelng=nelng, swlat=swlat, swlng=swlng)).first()
        if not res:
            return False

        req = """
            SELECT {properties}, 1 AS quantity FROM carshares c
            WHERE c.city = '{city}' AND c.parked = true AND
                ST_intersects(
                    ST_Transform(
                        ST_MakeEnvelope({nelng}, {nelat}, {swlng}, {swlat}, 4326),
                        3857
                    ),
                    c.geom
                )
            AND c.company != 'zipcar'
            UNION ALL
            SELECT DISTINCT ON (c.lot_id) {properties}, l.capacity AS quantity FROM carshares c
            JOIN carshare_lots l ON c.lot_id = l.id
            WHERE c.city = '{city}' AND c.parked = true
                AND ST_intersects(
                    ST_Transform(
                        ST_MakeEnvelope({nelng}, {nelat}, {swlng}, {swlat}, 4326),
                        3857
                    ),
                    c.geom
                )
            AND c.company = 'zipcar'
        """.format(
            properties=','.join(["c."+z for z in Carshares.properties]),
            city=res[0],
            nelat=nelat,
            nelng=nelng,
            swlat=swlat,
            swlng=swlng
        )

        return db.engine.execute(req).fetchall()

    @staticmethod
    def get_lots_within(city, x, y, radius, company=False):
        """
        Get all carshare lots in a city within a particular radius.

        :param city: city name that is being searched in (str)
        :param x: longitude (int)
        :param y: latitude (int)
        :param radius: radius in meters to search in (int)
        :param company: filter by carshare company name (str), or False to get all
        :returns: list of Carshare lot objects (dicts)
        """
        qry = """
            SELECT {properties} FROM carshare_lots
            WHERE city = '{city}' AND
                ST_Dwithin(
                    st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857),
                    geom,
                    {radius}
                )
        """
        if company and "," in company:
            qry += "AND company = ANY(ARRAY[{}])".format(",".join(["'"+z+"'" for z in company.split(",")]))
        elif company:
            qry += "AND company = '{}'".format(company)
        return db.engine.execute(qry.format(properties=', '.join(Carshares.lot_properties),
            city=city, x=x, y=y, radius=radius)).fetchall()

    @staticmethod
    def get_lots_nearest(city, x, y, limit, company=False):
        """
        Get nearest carshare lots in a city to a certain lat/long.

        :param city: city name that is being searched in (str)
        :param x: longitude (int)
        :param y: latitude (int)
        :param limit: number of nearest carshare lots to retrieve (int)
        :param company: filter by carshare company name (str), or False to get all
        :returns: list of Carshare lot objects (dicts)
        """
        qry = """
            SELECT {properties} FROM carshare_lots
            WHERE city = '{city}'
        """
        if company and "," in company:
            qry += "AND company = ANY(ARRAY[{}])".format(",".join(["'"+z+"'" for z in company.split(",")]))
        elif company:
            qry += "AND company = '{}'".format(company)
        qry += """
            ORDER BY ST_Distance(geom, st_transform('SRID=4326;POINT({x} {y})'::geometry, 3857))
            LIMIT {limit}
        """
        return db.engine.execute(qry.format(properties=', '.join(Carshares.lot_properties),
            city=city, x=x, y=y, limit=limit)).fetchall()

    @staticmethod
    def get_all(company, city):
        """
        Get all active carshare records for a city and company.

        :param company: company name (str)
        :param city: city to search in (str)
        :returns: list of Carshare objects (dicts)
        """
        res = db.engine.execute("""
            SELECT
                c.id,
                c.city,
                c.name,
                c.vin,
                c.address,
                to_char(c.since, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS since,
                c.geojson,
                s.rules
            FROM carshares c
            JOIN slots s ON c.city = s.city AND c.slot_id = s.id
            WHERE  c.company = '{company}'
                AND c.city   = '{city}'
                AND c.parked = true
                AND c.lot_id IS NULL
        """.format(company=company, city=city)).fetchall()
        return [
            {key: value for key, value in row.items()}
            for row in res
        ]
