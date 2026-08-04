-- =========================================================
-- Real Estate Voice Agent — Structured Knowledge Base Schema
-- Database: PostgreSQL
-- =========================================================

CREATE TABLE developers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    founded_year INT,
    reputation_notes TEXT,
    contact_phone VARCHAR(30),
    contact_email VARCHAR(100)
);

CREATE TABLE locations (
    id SERIAL PRIMARY KEY,
    area_name VARCHAR(150) NOT NULL,
    city VARCHAR(100) NOT NULL,
    latitude NUMERIC(9,6),
    longitude NUMERIC(9,6)
);

CREATE TABLE properties (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    property_type VARCHAR(30) NOT NULL CHECK (property_type IN ('house','apartment','plot','commercial')),
    listing_status VARCHAR(20) NOT NULL CHECK (listing_status IN ('buy','rent')),
    purpose_tag VARCHAR(30) CHECK (purpose_tag IN ('residential','investment','commercial')),
    price NUMERIC(14,2),
    rent_per_month NUMERIC(12,2),
    area_sqft NUMERIC(10,2),
    bedrooms INT,
    bathrooms INT,
    location_id INT REFERENCES locations(id),
    developer_id INT REFERENCES developers(id),
    description TEXT,
    listed_date DATE DEFAULT CURRENT_DATE,
    is_available BOOLEAN DEFAULT TRUE,
    CHECK (price IS NOT NULL OR rent_per_month IS NOT NULL)
);

CREATE TABLE amenities (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL
);

CREATE TABLE property_amenities (
    property_id INT REFERENCES properties(id) ON DELETE CASCADE,
    amenity_id INT REFERENCES amenities(id) ON DELETE CASCADE,
    PRIMARY KEY (property_id, amenity_id)
);

CREATE TABLE schools (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    location_id INT REFERENCES locations(id),
    school_type VARCHAR(50)
);

CREATE TABLE property_schools (
    property_id INT REFERENCES properties(id) ON DELETE CASCADE,
    school_id INT REFERENCES schools(id) ON DELETE CASCADE,
    distance_km NUMERIC(4,2),
    PRIMARY KEY (property_id, school_id)
);

CREATE TABLE hospitals (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    location_id INT REFERENCES locations(id)
);

CREATE TABLE property_hospitals (
    property_id INT REFERENCES properties(id) ON DELETE CASCADE,
    hospital_id INT REFERENCES hospitals(id) ON DELETE CASCADE,
    distance_km NUMERIC(4,2),
    PRIMARY KEY (property_id, hospital_id)
);

CREATE TABLE payment_plans (
    id SERIAL PRIMARY KEY,
    property_id INT REFERENCES properties(id) ON DELETE CASCADE,
    plan_type VARCHAR(50) NOT NULL,
    down_payment_pct NUMERIC(5,2),
    installment_months INT,
    installment_amount NUMERIC(12,2),
    notes TEXT
);

CREATE TABLE faqs (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50),
    question TEXT NOT NULL,
    answer TEXT NOT NULL
);

-- =========================================================
-- Helpful indexes for the agent's structured lookups
-- =========================================================
CREATE INDEX idx_properties_status ON properties(listing_status);
CREATE INDEX idx_properties_type ON properties(property_type);
CREATE INDEX idx_properties_price ON properties(price);
CREATE INDEX idx_properties_location ON properties(location_id);
CREATE INDEX idx_properties_bedrooms ON properties(bedrooms);