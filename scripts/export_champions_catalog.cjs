/** Resolve the pinned Showdown Dex, including inherited data. No runtime Node dependency.
 * Usage: node scripts/export_champions_catalog.cjs /path/to/built/showdown M-C > resolved.json
 * Upstream data: MIT, https://github.com/smogon/pokemon-showdown
 */
const path = require('node:path');
const root = path.resolve(process.argv[2]);
const regulation = process.argv[3];
if (!['M-C', 'M-B'].includes(regulation)) throw new Error('Unsupported resolved regulation');
const {TeamValidator} = require(path.join(root, 'dist/sim/team-validator'));
const {toID} = require(path.join(root, 'dist/sim/dex'));
const v = new TeamValidator(`gen9championsvgc2026reg${regulation.replace('-', '').toLowerCase()}`);
const d = v.dex;
const species = {}, items = {}, moves = {}, abilities = {}, natures = {};
for (const m of d.moves.all()) {
  if (!v.checkMove({moves: [m.name]}, m, {})) {
    moves[m.id] = {name:m.name, details:{type:m.type, category:m.category, power:m.basePower, accuracy:m.accuracy, pp:m.pp}};
  }
}
for (const a of d.abilities.all()) {
  if (!v.checkAbility({ability:a.name}, a, {})) abilities[a.id] = {name:a.name, details:{description:a.shortDesc}};
}
for (const s of d.species.all()) {
  if (v.checkSpecies({species:s.name}, s, s, {})) continue;
  const learned = new Set();
  for (const inherited of d.species.getFullLearnset(s.id)) {
    for (const [id, sources] of Object.entries(inherited.learnset)) {
      if (moves[id] && sources.some(source => source.startsWith('9'))) learned.add(id);
    }
  }
  species[s.id] = {name:s.name, base_species_id:toID(s.baseSpecies),
    ability_ids:[...new Set(Object.values(s.abilities).map(toID))].filter(id => abilities[id]).sort(),
    move_ids:[...learned].sort(), types:s.types, base_stats:s.baseStats,
    is_mega:!!s.isMega, battle_only:!!s.battleOnly, details:{forme:s.forme || null}};
}
for (const i of d.items.all()) {
  if (v.checkItem({item:i.name}, i, {})) continue;
  const links = i.megaStone ? Object.entries(i.megaStone) : [];

  const item = {name:i.name, details:{description:i.shortDesc}};
  item.mega_mappings = Object.fromEntries(links.map(([base, mega]) => [toID(base), toID(mega)]).filter(([base, mega]) => species[base] && species[mega]));
  if (links.length === 1 && Object.keys(item.mega_mappings).length) {
    item.mega_species_id = toID(links[0][0]); item.mega_variant_id = toID(links[0][1]);
  }
  items[i.id] = item;
}
for (const n of d.natures.all()) natures[n.id] = {name:n.name, plus:n.plus || null, minus:n.minus || null};
console.log(JSON.stringify({species,items,moves,abilities,natures}, null, 2));
