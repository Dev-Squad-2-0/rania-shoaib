-- =========================================================
-- Synthetic seed data — ALL DATA BELOW IS FAKE / FOR TESTING ONLY
-- =========================================================

-- DEVELOPERS
INSERT INTO developers (name, founded_year, reputation_notes, contact_phone, contact_email) VALUES
('Bahria Town Developers', 1996, 'Large-scale gated communities, strong track record on delivery timelines.', '021-111-222-333', 'info@bahriatown-example.com'),
('DHA City Developments', 1953, 'Long-established, high resale value, strict construction standards.', '021-111-444-555', 'contact@dhacity-example.com'),
('Emaar Pakistan', 2005, 'International developer, premium high-rise apartments.', '021-111-666-777', 'sales@emaarpk-example.com'),
('Fazaia Housing Scheme', 1985, 'Government-affiliated, budget-friendly, slower approval process.', '021-111-888-999', 'info@fazaia-example.com'),
('Lake City Developers', 2010, 'Mid-range housing society, growing rapidly, good amenities-to-price ratio.', '042-111-000-111', 'info@lakecity-example.com');

-- LOCATIONS
INSERT INTO locations (area_name, city, latitude, longitude) VALUES
('DHA Phase 6', 'Karachi', 24.8047, 67.0654),
('Bahria Town Phase 8', 'Rawalpindi', 33.5651, 73.1261),
('Gulberg III', 'Lahore', 31.5100, 74.3436),
('Clifton Block 5', 'Karachi', 24.8138, 67.0299),
('Bahria Town Precinct 10', 'Karachi', 24.8994, 67.2497),
('DHA Phase 2', 'Lahore', 31.4697, 74.4142),
('F-10 Markaz', 'Islamabad', 33.6938, 73.0114),
('Johar Town', 'Lahore', 31.4697, 74.2728);

-- AMENITIES
INSERT INTO amenities (name) VALUES
('24/7 Security'), ('Swimming Pool'), ('Gymnasium'), ('Community Park'),
('Backup Generator'), ('Covered Parking'), ('Mosque'), ('Kids Play Area'),
('Elevator'), ('Rooftop Terrace'), ('CCTV Surveillance'), ('Underground Electricity');

-- SCHOOLS
INSERT INTO schools (name, location_id, school_type) VALUES
('Beaconhouse DHA Campus', 1, 'Private, O/A Levels'),
('Bahria College Phase 8', 2, 'Private, Matric/FSc'),
('Lahore Grammar School Gulberg', 3, 'Private, O/A Levels'),
('Karachi Grammar School Clifton', 4, 'Private, O/A Levels'),
('Roots Millennium Bahria', 5, 'Private, O/A Levels'),
('City School DHA Lahore', 6, 'Private, O/A Levels'),
('Islamabad Model School F-10', 7, 'Public'),
('LACAS Johar Town', 8, 'Private, O/A Levels');

-- HOSPITALS
INSERT INTO hospitals (name, location_id) VALUES
('South City Hospital', 1),
('Bahria International Hospital', 2),
('Doctors Hospital Gulberg', 3),
('Clifton Medical Complex', 4),
('Bahria Town Hospital Precinct 10', 5),
('National Hospital DHA Lahore', 6),
('Shifa International Hospital', 7),
('Johar Town General Hospital', 8);

-- PROPERTIES
INSERT INTO properties (title, property_type, listing_status, purpose_tag, price, rent_per_month, area_sqft, bedrooms, bathrooms, location_id, developer_id, description, is_available) VALUES
('Modern 3-Bed Bungalow DHA Phase 6', 'house', 'buy', 'residential', 45000000, NULL, 2700, 3, 4, 1, 2, 'Freshly built bungalow with contemporary finishes, close to Khayaban-e-Sehar. Corner plot with extra sunlight and ventilation.', TRUE),
('Luxury 4-Bed Villa Bahria Phase 8', 'house', 'buy', 'residential', 68000000, NULL, 4000, 4, 5, 2, 1, 'Spacious villa in a gated community with dedicated servant quarters and a private lawn.', TRUE),
('2-Bed Apartment Gulberg III', 'apartment', 'rent', 'residential', NULL, 185000, 1200, 2, 2, 3, 3, 'Mid-rise apartment building with elevator access, ideal for small families or professionals.', TRUE),
('Emaar Crescent Bay 3-Bed Apartment', 'apartment', 'buy', 'investment', 52000000, NULL, 1850, 3, 3, 4, 3, 'Sea-facing high-rise unit, strong rental yield history, premium building amenities.', TRUE),
('Compact 1-Bed Apartment Bahria Precinct 10', 'apartment', 'rent', 'residential', NULL, 65000, 650, 1, 1, 5, 1, 'Affordable rental unit close to Bahria Town main boulevard, good for a single tenant or student.', TRUE),
('5-Marla Plot DHA Phase 2 Lahore', 'plot', 'buy', 'investment', 21000000, NULL, 1125, NULL, NULL, 6, 2, 'Prime location plot on a 40-foot road, possession-ready with no construction restrictions pending.', TRUE),
('Commercial Shop F-10 Markaz', 'commercial', 'buy', 'commercial', 38000000, NULL, 400, NULL, 1, 7, 4, 'Ground floor commercial unit on the main market road, high foot traffic, suitable for retail or a clinic.', TRUE),
('3-Bed Apartment Johar Town', 'apartment', 'rent', 'residential', NULL, 95000, 1400, 3, 3, 8, 5, 'Family-friendly building with a community park view and covered parking.', TRUE),
('10-Marla House DHA Phase 6', 'house', 'buy', 'residential', 95000000, NULL, 4500, 5, 6, 1, 2, 'Double-storey house with basement, home theater, and rooftop terrace.', TRUE),
('Studio Apartment Clifton Block 5', 'apartment', 'rent', 'residential', NULL, 75000, 550, 1, 1, 4, 3, 'Compact studio near Clifton beach, popular with young professionals.', TRUE),
('1-Kanal Villa Bahria Phase 8', 'house', 'buy', 'residential', 120000000, NULL, 5445, 5, 6, 2, 1, 'Fully furnished 1-Kanal villa with swimming pool and home automation system.', TRUE),
('Investor Plot Bahria Precinct 10', 'plot', 'buy', 'investment', 8500000, NULL, 500, NULL, NULL, 5, 1, 'Entry-level investment plot in a rapidly appreciating sector, no construction required immediately.', TRUE),
('Commercial Office Gulberg III', 'commercial', 'rent', 'commercial', NULL, 220000, 1600, NULL, 2, 3, 3, 'Fully fitted office space suitable for a corporate branch or agency, includes 4 parking bays.', TRUE),
('2-Bed Apartment F-10 Islamabad', 'apartment', 'buy', 'residential', 34000000, NULL, 1350, 2, 2, 7, 4, 'Quiet residential block close to Margalla Hills viewpoint, good resale demand.', TRUE),
('4-Bed House Johar Town', 'house', 'buy', 'residential', 58000000, NULL, 3600, 4, 4, 8, 5, 'Recently renovated with modern kitchen and marble flooring throughout.', TRUE),
('Rental Shop Bahria Phase 8 Commercial', 'commercial', 'rent', 'commercial', NULL, 150000, 350, NULL, 1, 2, 1, 'Prime commercial strip location, currently vacant and ready for immediate occupancy.', TRUE),
('3-Bed Apartment DHA Phase 2 Lahore', 'apartment', 'rent', 'residential', NULL, 130000, 1500, 3, 3, 6, 2, 'New building with backup generator and dedicated visitor parking.', TRUE),
('Farmhouse Plot Bahria Precinct 10', 'plot', 'buy', 'investment', 15000000, NULL, 1000, NULL, NULL, 5, 1, 'Larger plot suited for a farmhouse-style build, semi-developed sector.', TRUE),
('Penthouse Emaar Crescent Bay', 'apartment', 'buy', 'residential', 145000000, NULL, 3800, 4, 5, 4, 3, 'Top-floor penthouse with panoramic sea views and private elevator access.', TRUE),
('2-Bed Apartment Gulberg III Rental', 'apartment', 'rent', 'residential', NULL, 110000, 1100, 2, 2, 3, 3, 'Well-maintained building, walking distance to Liberty Market.', TRUE);

-- PROPERTY <-> AMENITIES (sample mappings, not exhaustive)
INSERT INTO property_amenities (property_id, amenity_id) VALUES
(1, 1), (1, 4), (1, 6),
(2, 1), (2, 2), (2, 3), (2, 4), (2, 5),
(3, 1), (3, 9),
(4, 1), (4, 2), (4, 3), (4, 9), (4, 10),
(5, 1), (5, 6),
(7, 1), (7, 11),
(9, 1), (9, 5), (9, 6), (9, 10),
(11, 1), (11, 2), (11, 5), (11, 12),
(13, 1), (13, 6), (13, 9),
(19, 1), (19, 2), (19, 3), (19, 9), (19, 10);

-- PROPERTY <-> SCHOOLS
INSERT INTO property_schools (property_id, school_id, distance_km) VALUES
(1, 1, 1.2), (2, 2, 0.8), (3, 3, 1.5), (4, 4, 2.0),
(5, 5, 0.6), (6, 6, 1.0), (7, 7, 1.8), (8, 8, 0.9),
(9, 1, 1.4), (11, 2, 1.1), (14, 7, 1.3), (15, 8, 1.0);

-- PROPERTY <-> HOSPITALS
INSERT INTO property_hospitals (property_id, hospital_id, distance_km) VALUES
(1, 1, 2.1), (2, 2, 1.4), (3, 3, 1.9), (4, 4, 1.2),
(5, 5, 0.7), (6, 6, 2.5), (7, 7, 1.6), (8, 8, 1.1),
(9, 1, 2.3), (11, 2, 1.5), (14, 7, 2.0), (15, 8, 1.2);

-- PAYMENT PLANS (only for 'buy' properties, illustrative)
INSERT INTO payment_plans (property_id, plan_type, down_payment_pct, installment_months, installment_amount, notes) VALUES
(1, 'Installments', 30.00, 24, 1312500, '30% down payment, remaining over 2 years.'),
(2, 'Installments', 40.00, 36, 1133333, 'Requires developer NOC before transfer.'),
(4, 'Installments', 25.00, 48, 812500, 'Investment-friendly plan with post-handover installments.'),
(6, 'Full Payment / Installments', 50.00, 12, 875000, 'Discount of 5% available on full upfront payment.'),
(7, 'Installments', 35.00, 24, 1029167, 'Commercial unit, possession on full payment only.'),
(9, 'Installments', 30.00, 36, 1758333, 'Basement and rooftop included in base price.'),
(11, 'Full Payment', 100.00, 0, 0, 'Ready-to-move, full payment required, no installment plan offered.'),
(12, 'Installments', 20.00, 24, 283333, 'Low entry point, popular with first-time investors.'),
(14, 'Installments', 30.00, 24, 991667, 'Standard 2-year installment plan.'),
(15, 'Installments', 25.00, 36, 1208333, 'Flexible plan, early completion discount available.'),
(18, 'Installments', 20.00, 18, 666667, 'Semi-developed sector, lower down payment to attract early investors.'),
(19, 'Full Payment / Installments', 50.00, 24, 3020833, 'Premium unit, negotiable terms for full payment buyers.');

-- FAQs (also feeds the unstructured/Chroma layer — see unstructured_data.json)
INSERT INTO faqs (category, question, answer) VALUES
('general', 'Kya aap plot registry mein madad karte hain?', 'Ji haan, humari legal team registry aur transfer ke pure process mein madad karti hai, koi extra charge nahi hai advisory ke liye.'),
('payment', 'Down payment kitna hota hai?', 'Ye property se property vary karta hai, generally 20% se 50% tak, poori detail property ki payment plan mein mil jayegi.'),
('visit', 'Site visit ke liye kya charges hain?', 'Site visit bilkul free hai, hum khud aap ko property tak le jate hain.'),
('legal', 'Kya property clear title hai?', 'Sab humari listings verified aur clear title ke saath hoti hain, documentation visit ke waqt dikhayi jati hai.'),
('rent', 'Rent pe security deposit kitna hota hai?', 'Generally 2 mahine ka rent bataur security deposit liya jata hai, ye property ke hisaab se thoda vary kar sakta hai.');
