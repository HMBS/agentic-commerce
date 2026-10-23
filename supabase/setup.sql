-- Fitzwilliam Provisions — Supabase catalog schema.
--
-- Products are the single source of truth for the storefront, the MCP server
-- and the Stripe test backend. The Android app never reads this table directly.
--
-- Run this in the Supabase SQL editor, then run seed.sql.

create table if not exists public.products (
    id               text primary key,
    sku              text not null unique,
    name             text not null,
    subtitle         text not null default '',
    category         text not null,
    price_minor      integer not null check (price_minor > 0),
    currency         text not null default 'EUR',
    stock            integer not null default 0 check (stock >= 0),
    age_restriction  text,
    image_url        text not null,
    notes            text[] not null default '{}',
    search_tags      text[] not null default '{}',
    active           boolean not null default true,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists products_active_category_idx
    on public.products (active, category);

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists products_touch_updated_at on public.products;
create trigger products_touch_updated_at
    before update on public.products
    for each row execute function public.touch_updated_at();

alter table public.products enable row level security;

drop policy if exists "Public can read active products" on public.products;
create policy "Public can read active products"
    on public.products
    for select
    using (active = true);

-- The MCP server and Stripe backend use the public anon key to read the shelf.
-- Admin writes happen in the Supabase dashboard, which bypasses RLS.
grant usage on schema public to anon;
grant select on public.products to anon;

-- Product images:
--   1. Create a Storage bucket named "product-images".
--   2. Mark it public (or add a public read policy).
--   3. Put the public URL in products.image_url.
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
