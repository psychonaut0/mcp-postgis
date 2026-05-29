-- Small, hand-crafted spatial fixture used across integration tests.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS app;

-- A handful of points: three Italian cities (lat/lon).
CREATE TABLE app.cities (
    id    BIGSERIAL PRIMARY KEY,
    name  TEXT NOT NULL,
    geom  GEOMETRY(Point, 4326) NOT NULL
);
INSERT INTO app.cities (name, geom) VALUES
    ('Rome',    ST_SetSRID(ST_MakePoint(12.4964, 41.9028), 4326)),
    ('Milan',   ST_SetSRID(ST_MakePoint(9.1900,  45.4642), 4326)),
    ('Cagliari',ST_SetSRID(ST_MakePoint(9.1217,  39.2238), 4326));
CREATE INDEX cities_geom_gix ON app.cities USING GIST (geom);

-- A polygon covering mainland Italy (roughly).
CREATE TABLE app.regions (
    id    BIGSERIAL PRIMARY KEY,
    name  TEXT NOT NULL,
    geom  GEOMETRY(Polygon, 4326) NOT NULL
);
INSERT INTO app.regions (name, geom) VALUES
    ('Italy-bbox', ST_SetSRID(ST_GeomFromText(
        'POLYGON((6.6 36.6, 18.5 36.6, 18.5 47.1, 6.6 47.1, 6.6 36.6))'
    ), 4326));
CREATE INDEX regions_geom_gix ON app.regions USING GIST (geom);

-- A non-spatial table so listings can prove they filter correctly.
CREATE TABLE app.notes (
    id    BIGSERIAL PRIMARY KEY,
    body  TEXT NOT NULL
);

-- Deliberately malformed geometries for validity-checker tests.
-- Stored with a generic Geometry type so invalid/out-of-range values are accepted.
CREATE TABLE app.bad_geoms (
    id    BIGSERIAL PRIMARY KEY,
    label TEXT NOT NULL,
    geom  GEOMETRY(Geometry, 4326) NOT NULL
);
INSERT INTO app.bad_geoms (label, geom) VALUES
    -- self-intersecting "bowtie" polygon -> ST_IsValid = false
    ('bowtie', ST_SetSRID(ST_GeomFromText(
        'POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))'), 4326)),
    -- longitude 200 is outside [-180, 180] -> out of range
    ('out_of_range', ST_SetSRID(ST_GeomFromText(
        'POINT(200 10)'), 4326));
