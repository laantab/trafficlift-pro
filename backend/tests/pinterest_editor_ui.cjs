const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
const start = html.indexOf('    function editPinterestPackage(');
const end = html.indexOf('    function renderPinterestResult(', start);
assert(start >= 0 && end > start, 'Pinterest editorial function must exist');
const context = vm.createContext({});
vm.runInContext(html.slice(start, end), context);
const product = {name: 'Purawood Bamboo Drawer Organizer - Expandable Silverware Organizer',category:'Kitchen'};
const source = {pin_title: product.name, optimized_description: 'Best-selling guaranteed winner!',board_title:'Purawood',pin_cta_suggestion:'Buy now'};
const revised = context.editPinterestPackage(source, product);
assert.match(revised.pin_title, /Bamboo Drawer Organizer/);
assert(revised.pin_title.startsWith('Purawood'), 'Pin must retain the exact product brand');
assert(revised.pin_title.length <= 100);
assert(!/guaranteed|best-selling/i.test(revised.optimized_description));
assert.match(revised.optimized_description, /seller listing/i);
assert.equal(revised.editorial_review.status, 'draft');
assert.equal(revised.editorial_review.claims_verified, false);
assert.equal(source.pin_title, product.name, 'Do not mutate source package');
const unknown = context.editPinterestPackage({pin_title:'Biggest Sales Ever'}, {name:'Acme Portable Widget',category:'Gadgets'});
assert(!/Biggest Sales/i.test(unknown.pin_title));
assert.match(unknown.pin_title, /Acme Portable Widget/);
assert(!/guaranteed/i.test(unknown.optimized_description));
// Product attributes in marketing copy must be supported by the product title,
// not inferred from a broad category or from only one of two adjectives.
const bambooOnly = context.editPinterestPackage({}, {name:'Bamboo Drawer Organizer',category:'Kitchen'});
assert.match(bambooOnly.optimized_description, /bamboo drawer organizer ideas/i);
assert.doesNotMatch(bambooOnly.optimized_description, /expandable bamboo/i,
  'Bamboo does not prove a drawer organizer expands');
const expandableOnly = context.editPinterestPackage({}, {name:'Expandable Drawer Organizer',category:'Kitchen'});
assert.match(expandableOnly.optimized_description, /expandable drawer organizer ideas/i);
assert.doesNotMatch(expandableOnly.optimized_description, /expandable bamboo/i,
  'Expandable does not prove the material is bamboo');
const both = context.editPinterestPackage({}, {name:'Expandable Bamboo Drawer Organizer',category:'Kitchen'});
assert.match(both.optimized_description, /expandable bamboo drawer organizer ideas/i);
const categoryOnly = context.editPinterestPackage({}, {name:'Storage Tray',category:'Kitchen Drawer Organizers'});
assert.doesNotMatch(categoryOnly.optimized_description, /drawer organizer ideas/i,
  'A category label cannot establish a specific product attribute');
const night = context.editPinterestPackage({pin_title:'Explore Tech & Gadgets Ideas'},
  {name:'JAS11242 LED Motion-Sensor Night Light Nickel Finish',category:'Tech & Gadgets'});
assert.match(night.pin_title, /JAS11242.*Motion-Sensor Night Light/);
const zizev = context.editPinterestPackage({pin_title:'Explore Storage Ideas'},
  {name:'ZIZEV Versatile Storage Organizer Tray',category:'Home Storage'});
assert.match(zizev.pin_title, /ZIZEV.*Storage Organizer Tray/);
const lamp = context.editPinterestPackage({},
  {name:'Mainstays 17 LED Adjustable Flexible Desk Lamp',category:'Home Lighting'});
assert.match(lamp.pin_title, /Mainstays.*Desk Lamp/);
assert.doesNotMatch(night.pin_title, /Explore|Ideas|Gadgets/i);
assert.throws(() => context.editPinterestPackage({}, {name:'',category:'Tech & Gadgets'}), /exact product name/);
console.log('PASS: product-specific pin identity, honest attributes, no generic category slogans');
