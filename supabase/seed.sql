-- Fitzwilliam Provisions — initial Supabase catalog.
--
-- Safe to re-run: existing ids are updated, new ids are inserted.
-- Run after schema.sql.

insert into public.products (
    id, sku, name, subtitle, category, price_minor, currency, stock,
    age_restriction, image_url, notes, search_tags, active
) values
    (
        'cof-yir-250', 'FV-COF-001', 'Gasharu Yirgacheffe',
        'Washed, whole bean, 250 g', 'pantry', 1850, 'EUR', 42,
        null, 'https://store.leobo.me/assets/img/coffee.svg',
        array['Jasmine', 'Bergamot', 'White peach'],
        array['coffee', 'beans', 'espresso', 'filter', 'yirgacheffe', 'gasharu'],
        true
    ),
    (
        'cho-sea-090', 'FV-CHO-002', 'Sea Salt, Seventy',
        'Single origin cacao, 70%, 90 g', 'pantry', 690, 'EUR', 118,
        null, 'https://store.leobo.me/assets/img/chocolate.svg',
        array['Trinitario', 'Fleur de sel', 'Long finish'],
        array['chocolate', 'cacao', 'sea salt', 'seventy', 'bar'],
        true
    ),
    (
        'oil-kor-500', 'FV-OIL-003', 'Koroneiki First Press',
        'Early harvest, 500 ml', 'pantry', 2200, 'EUR', 27,
        null, 'https://store.leobo.me/assets/img/olive-oil.svg',
        array['Green almond', 'Peppery', 'Kalamata'],
        array['olive oil', 'oil', 'koroneiki', 'greek', 'salad'],
        true
    ),
    (
        'kit-sour-001', 'FV-KIT-004', 'Sourdough Starter Kit',
        'Jar, stoneground rye, linen cloth', 'kitchen', 3200, 'EUR', 15,
        null, 'https://store.leobo.me/assets/img/sourdough.svg',
        array['Day 1 culture', 'Rye fed', 'Cloth wrap'],
        array['sourdough', 'bread', 'starter', 'baking', 'rye', 'culture'],
        true
    ),
    (
        'kni-gyu-210', 'FV-KNI-005', 'Carbon Steel Gyuto',
        '210 mm, wa-handle, Shirogami 2', 'kitchen', 12900, 'EUR', 6,
        'bladed_article_18', 'https://store.leobo.me/assets/img/knife.svg',
        array['Reactive steel', 'Octagonal handle', '18+'],
        array['knife', 'gyuto', 'blade', 'chef', 'carbon steel', 'shirogami'],
        true
    ),
    (
        'win-pet-2023', 'FV-WIN-006', 'Loam & Lilt Pét-Nat',
        'Skin contact, 2023, 75 cl', 'wine', 2800, 'EUR', 24,
        'alcohol_18', 'https://store.leobo.me/assets/img/wine-petnat.svg',
        array['Volcanic soil', 'Unfined', '11.5% ABV'],
        array['wine', 'pet nat', 'pét-nat', 'sparkling', 'skin contact', 'bottle'],
        true
    ),
    (
        'win-bar-2019', 'FV-WIN-007', 'Barolo, Vigna Rionda',
        'Nebbiolo, 2019, 75 cl', 'wine', 4600, 'EUR', 9,
        'alcohol_18', 'https://store.leobo.me/assets/img/wine-barolo.svg',
        array['Serralunga', '48-month élevage', '14.5% ABV'],
        array['wine', 'barolo', 'nebbiolo', 'red', 'bottle', 'piedmont'],
        true
    )
on conflict (id) do update set
    sku = excluded.sku,
    name = excluded.name,
    subtitle = excluded.subtitle,
    category = excluded.category,
    price_minor = excluded.price_minor,
    currency = excluded.currency,
    stock = excluded.stock,
    age_restriction = excluded.age_restriction,
    image_url = excluded.image_url,
    notes = excluded.notes,
    search_tags = excluded.search_tags,
    active = excluded.active;
