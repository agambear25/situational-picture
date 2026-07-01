-- Coarse paving of the cell's dominant road: 'paved' | 'unpaved' | 'unknown'.
-- 'unpaved' = OSM surface in (unpaved,dirt,ground,gravel,compacted,fine_gravel,earth,mud,sand)
--             OR highway in (track, path) with no explicit paved surface — the "dirt road" proxy.
ALTER TABLE geo.cell_context ADD COLUMN IF NOT EXISTS road_surface TEXT;
