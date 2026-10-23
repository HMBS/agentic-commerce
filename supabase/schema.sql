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
