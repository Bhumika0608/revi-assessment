"""
Menu generator for Talkin' Tacos — 10,000-item scale.

Generates a full expanded menu to demonstrate that the FTS5 + rapidfuzz
search architecture handles restaurant-scale catalog sizes without degradation.

Run: python scripts/generate_menu.py
Output: data/menu_expanded.json (~10,000 items)
"""

import json
import random
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).parent.parent

# ── Base item IDs to skip (already defined in menu.json) ─────────────────────
BASE_IDS = {
    "taco_birria", "taco_carne_asada", "taco_al_pastor", "taco_pollo",
    "taco_carnitas", "taco_veggie", "taco_fish", "taco_shrimp",
    "burrito_birria", "burrito_california", "burrito_veggie", "burrito_build_your_own",
    "bowl_birria", "bowl_pollo", "bowl_veggie",
    "quesadilla_cheese", "quesadilla_meat",
    "nachos_loaded",
    "side_chips_salsa", "side_chips_guac", "side_rice", "side_beans_black", "side_elote",
    "drink_horchata", "drink_jarritos", "drink_coke_mexican", "drink_water",
    "dessert_churros", "dessert_flan",
}

# ── 30 Proteins ───────────────────────────────────────────────────────────────
# (id, display_label, description_phrase, dietary_tags, price_surcharge)
PROTEINS = [
    ("birria",       "Birria",            "slow-braised beef",                  ["beef"],                      0.00),
    ("carne_asada",  "Carne Asada",       "grilled marinated steak",            ["beef"],                      0.00),
    ("al_pastor",    "Al Pastor",         "marinated pork with pineapple",      ["pork"],                      0.00),
    ("pollo",        "Pollo Asado",       "grilled citrus chicken",             ["chicken"],                   0.00),
    ("carnitas",     "Carnitas",          "slow-cooked pulled pork",            ["pork"],                      0.00),
    ("veggie",       "Veggie",            "grilled seasonal vegetables",        ["vegetarian", "vegan"],        0.00),
    ("barbacoa",     "Barbacoa",          "slow-cooked beef cheek",             ["beef"],                      0.50),
    ("chorizo",      "Chorizo",           "spiced Mexican sausage",             ["pork"],                      0.00),
    ("tinga",        "Tinga",             "chipotle shredded chicken",          ["chicken"],                   0.00),
    ("suadero",      "Suadero",           "slow-braised beef brisket",          ["beef"],                      0.50),
    ("lengua",       "Lengua",            "braised beef tongue",                ["beef"],                      0.75),
    ("nopales",      "Nopales",           "grilled cactus paddle",              ["vegetarian", "vegan"],        0.00),
    ("pescado",      "Pescado",           "grilled seasoned fish",              ["fish"],                      0.75),
    ("camaron",      "Camarón",           "grilled shrimp",                     ["shellfish"],                 1.00),
    ("ribeye",       "Ribeye",            "grilled prime ribeye steak",         ["beef"],                      2.00),
    ("lamb",         "Lamb",              "slow-roasted lamb",                  ["lamb"],                      1.50),
    ("duck",         "Duck Confit",       "slow-rendered duck confit",          ["duck"],                      2.00),
    ("pork_belly",   "Pork Belly",        "crispy braised pork belly",          ["pork"],                      1.50),
    ("rajas",        "Rajas con Crema",   "roasted poblano strips with crema",  ["vegetarian", "contains_dairy"], 0.25),
    ("hongos",       "Hongos",            "wild mushroom medley",               ["vegetarian", "vegan"],        0.50),
    ("papa_chorizo", "Papa con Chorizo",  "potato and chorizo hash",            ["pork"],                      0.00),
    ("cochinita",    "Cochinita Pibil",   "Yucatecan achiote slow-roasted pork",["pork"],                      0.50),
    ("achiote",      "Pollo Achiote",     "achiote-marinated chicken",          ["chicken"],                   0.25),
    ("brisket",      "Smoked Brisket",    "Texas-style smoked brisket",         ["beef"],                      1.75),
    ("pulpo",        "Pulpo",             "grilled octopus",                    ["shellfish"],                 2.50),
    ("calamari",     "Calamari",          "crispy calamari",                    ["shellfish"],                 1.50),
    ("tripa",        "Tripa",             "traditional beef tripe",             ["beef"],                      0.00),
    ("cabeza",       "Cabeza",            "slow-braised beef head",             ["beef"],                      0.25),
    ("lobster",      "Lobster",           "grilled Maine lobster tail",         ["shellfish"],                 5.00),
    ("crab",         "Dungeness Crab",    "fresh Dungeness crab",               ["shellfish"],                 4.00),
]

# ── 50 Taco Styles ────────────────────────────────────────────────────────────
# (id, label, prep_description, price_delta)
TACO_STYLES = [
    ("street",      "Street-Style",       "Traditional street preparation",                  0.00),
    ("dorado",      "Dorado",             "Crispy pan-fried golden tortilla",                0.50),
    ("gobernador",  "Gobernador",         "Melted cheese and pepper crust",                  0.75),
    ("canasta",     "Canasta",            "Steamed basket-style, soft and juicy",           -0.25),
    ("adobado",     "Adobado",            "Smoky adobo-marinated preparation",               0.25),
    ("plancha",     "a la Plancha",       "Flat-griddle seared, crisp edges",                0.25),
    ("birria_dip",  "Birria-Dipped",      "Consomé-dipped and pan-crisped",                  0.75),
    ("enchilado",   "Enchilado",          "Red chile sauce coated",                          0.50),
    ("sudado",      "Sudado",             "Steamed, tender and juicy",                       0.00),
    ("campestre",   "Campestre",          "Rustic ranch-style preparation",                 -0.25),
    ("norteno",     "Norteño",            "Northern Mexico style",                           0.00),
    ("oaxacan",     "Oaxacan-Style",      "Oaxacan black bean and Oaxacan cheese base",      0.50),
    ("yucatecan",   "Yucatecan",          "Achiote-spiced Yucatán preparation",              0.25),
    ("california",  "California-Style",   "Avocado, sprouts, and fresh toppings",            0.50),
    ("baja",        "Baja-Style",         "Cabbage slaw, crema, and lime",                   0.25),
    ("sonoran",     "Sonoran",            "Northern Sonoran desert style",                   0.00),
    ("texmex",      "Tex-Mex",            "Tex-Mex fusion with yellow cheese",               0.00),
    ("artisan",     "Artisan",            "Small-batch artisan preparation",                 1.00),
    ("gourmet",     "Gourmet",            "Restaurant-quality elevated presentation",        1.25),
    ("premium",     "Premium",            "Premium ingredients, refined technique",          1.50),
    ("loaded",      "Loaded",             "Fully loaded with every topping",                 0.75),
    ("signature",   "Signature",          "Chef's signature house preparation",              1.00),
    ("smoked",      "Smoked",             "Slow-smoked over mesquite wood",                  0.75),
    ("braised",     "Braised",            "Slow-braised in rich aromatics",                  0.50),
    ("grilled",     "Chargrilled",        "Open-flame char-grilled",                         0.25),
    ("crispy",      "Extra Crispy",       "Extra crispy fried shell",                        0.25),
    ("soft",        "Extra Soft",         "Soft and pillowy house corn tortilla",            0.00),
    ("spicy",       "Fuego",              "House fire-roasted chile preparation",             0.25),
    ("mild",        "Suave",              "Mild and savory, gentle heat",                   -0.25),
    ("heritage",    "Heritage Recipe",    "Traditional family heritage recipe",              0.50),
    ("market",      "Market-Fresh",       "Made with today's farmers market ingredients",    0.75),
    ("seasonal",    "Seasonal Special",   "Chef's rotating seasonal creation",               0.75),
    ("truffle",     "Black Truffle",      "Black truffle oil-infused premium preparation",   2.50),
    ("chipotle",    "Chipotle Smoked",    "Chipotle pepper smoked preparation",              0.50),
    ("mole_negro",  "Mole Negro",         "Rich Oaxacan black mole sauce",                   0.75),
    ("mole_verde",  "Mole Verde",         "Fresh herb and pumpkin seed mole",                0.75),
    ("verde",       "Salsa Verde",        "Roasted tomatillo salsa verde",                   0.25),
    ("roja",        "Salsa Roja",         "House red chile salsa roja",                      0.25),
    ("suiza",       "Suiza",              "Swiss-style tomatillo cream sauce",               0.50),
    ("ahogado",     "Ahogado",            "Drowned in spicy tomato broth",                   0.50),
    ("guisado",     "Guisado",            "Slow-stewed in rich house sauce",                 0.25),
    ("tatemado",    "Tatemado",           "Charred and slow-cooked over open fire",          0.75),
    ("mechado",     "Mechado",            "Pulled and shredded with spiced oil",             0.25),
    ("ahumado",     "Ahumado",            "Cold-smoked over fruitwood chips",                0.75),
    ("estofado",    "Estofado",           "Traditional Spanish-Mexican stew style",          0.50),
    ("en_salsa",    "en Salsa",           "Simmered in house salsa",                         0.25),
    ("empapado",    "Empapado",           "Soaked in rich consomé broth",                    0.50),
    ("marinado",    "Marinado",           "24-hour citrus-and-herb marinated",               0.25),
    ("al_carbon",   "al Carbón",          "Charcoal-grilled over live coals",                0.50),
    ("en_mole",     "en Mole",            "Served in traditional house mole",                0.75),
    ("habanero",    "Habanero Glazed",    "Fiery habanero glaze with mango slaw",            0.75),
    ("adobo",       "Adobo-Marinated",    "Deep adobo spice rub, slow-cooked",               0.50),
    ("tlayuda",     "Tlayuda-Style",      "Open-face on giant crispy tortilla",              1.00),
]

TACO_SIZES = [
    ("mini",    "Mini",    -0.75),
    ("regular", "",         0.00),
    ("large",   "Large",    1.25),
]

TACO_BASE_PRICE = 4.99

TACO_OPTIONS = {
    "tortilla": {"choices": ["corn", "flour"], "default": "corn", "required": False},
    "salsa":    {"choices": ["mild", "medium", "hot", "habanero"], "default": "medium", "required": False},
}
TACO_MODIFIERS = [
    {"id": "add_cheese",  "name": "Add cheese",  "price": 0.75},
    {"id": "extra_meat",  "name": "Extra meat",  "price": 3.00},
    {"id": "no_onion",    "name": "No onion",    "price": 0.00},
    {"id": "no_cilantro", "name": "No cilantro", "price": 0.00},
]

# ── 20 Burrito Styles ─────────────────────────────────────────────────────────
BURRITO_STYLES = [
    ("classic",      "Classic",         "Classic rolled burrito",                          0.00),
    ("wet",          "Wet",             "Smothered in red or green sauce",                 1.00),
    ("skinny",       "Skinny",          "No rice, extra protein",                          0.50),
    ("breakfast",    "Breakfast",       "Scrambled eggs and breakfast potatoes",           0.00),
    ("california",   "California",      "Avocado, sour cream, and pico",                   1.00),
    ("smothered",    "Smothered",       "Double-smothered in house chiles",                1.00),
    ("crispy_fried", "Crispy-Fried",    "Deep-fried until golden and crispy",              1.25),
    ("quesaburrito", "Quesaburrito",    "Cheese-seared quesadilla-burrito hybrid",        1.50),
    ("mission",      "Mission-Style",   "Extra-large Mission-style with everything",       1.00),
    ("super",        "Super",           "Super-sized with double fillings",                2.00),
    ("loaded",       "Loaded",          "Fully loaded with every topping",                 1.00),
    ("gourmet",      "Gourmet",         "Elevated gourmet preparation",                    1.75),
    ("premium",      "Premium",         "Premium ingredients throughout",                  2.00),
    ("verde",        "Verde",           "Smothered in roasted tomatillo sauce",            1.00),
    ("roja",         "Roja",            "Smothered in house red chile sauce",              1.00),
    ("mole",         "Mole",            "Dressed with Oaxacan black mole",                 1.25),
    ("low_carb",     "Low-Carb",        "No rice, extra vegetables, lettuce wrap option",  0.50),
    ("keto",         "Keto-Style",      "No rice or beans, extra greens and protein",      0.50),
    ("signature",    "Signature",       "Chef's signature house preparation",              1.50),
    ("seasonal",     "Seasonal",        "Chef's rotating seasonal filling",                1.00),
]

BURRITO_SIZES = [
    ("regular", "",       0.00),
    ("large",   "Large",  2.50),
    ("xlarge",  "XL",     4.50),
]

BURRITO_BASE_PRICE = 12.49
BURRITO_OPTIONS = {
    "salsa": {"choices": ["mild", "medium", "hot", "habanero"], "default": "medium", "required": False},
}
BURRITO_MODIFIERS = [
    {"id": "add_cheese",     "name": "Add cheese",      "price": 0.75},
    {"id": "extra_meat",     "name": "Extra meat",      "price": 3.00},
    {"id": "add_guac",       "name": "Add guacamole",   "price": 1.50},
    {"id": "add_sour_cream", "name": "Add sour cream",  "price": 0.50},
    {"id": "no_rice",        "name": "No rice",         "price": 0.00},
    {"id": "no_beans",       "name": "No beans",        "price": 0.00},
    {"id": "no_onion",       "name": "No onion",        "price": 0.00},
    {"id": "no_cilantro",    "name": "No cilantro",     "price": 0.00},
]

# ── 12 Bowl Styles ────────────────────────────────────────────────────────────
BOWL_STYLES = [
    ("classic",    "Classic",      "white rice and black beans",                    0.00),
    ("burrito",    "Burrito-Style","all burrito fillings, tortilla on the side",     0.50),
    ("low_carb",   "Low-Carb",    "cauliflower rice base, extra greens",             0.50),
    ("keto",       "Keto",         "no rice or beans, extra greens and guac",        0.50),
    ("loaded",     "Loaded",       "every topping including guac and sour cream",    1.00),
    ("superfood",  "Superfood",    "quinoa, kale, roasted sweet potato",             1.25),
    ("street",     "Street",       "street-style with onion and cilantro only",     -0.50),
    ("gourmet",    "Gourmet",      "roasted corn, pickled onion, micro herbs",       1.50),
    ("heritage",   "Heritage",     "traditional preparation with heirloom beans",    0.75),
    ("power",      "Power",        "brown rice, lentils, roasted peppers",           0.75),
    ("verde",      "Verde",        "tomatillo rice and salsa verde dressing",        0.50),
    ("seasonal",   "Seasonal",     "chef's rotating seasonal grain and toppings",    0.75),
]

BOWL_SIZES = [
    ("regular", "",       0.00),
    ("large",   "Large",  2.00),
    ("family",  "Family", 5.00),
]

BOWL_BASE_PRICE = 12.49
BOWL_OPTIONS = {
    "salsa": {"choices": ["mild", "medium", "hot", "habanero"], "default": "medium", "required": False},
    "rice":  {"choices": ["white", "brown", "none"], "default": "white", "required": False},
    "beans": {"choices": ["black", "pinto", "none"],  "default": "black", "required": False},
}
BOWL_MODIFIERS = [
    {"id": "add_guac",       "name": "Add guacamole",  "price": 1.50},
    {"id": "add_sour_cream", "name": "Add sour cream", "price": 0.50},
    {"id": "add_cheese",     "name": "Add cheese",     "price": 0.75},
    {"id": "extra_meat",     "name": "Extra meat",     "price": 3.00},
]

# ── 10 Quesadilla Styles ──────────────────────────────────────────────────────
QUESADILLA_STYLES = [
    ("grande",      "Grande",       "Large double-tortilla with extra cheese",        2.00),
    ("birria_dip",  "Birria Dip",   "Birria-dipped crispy with consomé",              2.50),
    ("street",      "Street-Style", "Thin, crispy street-style quesadilla",           0.00),
    ("loaded",      "Loaded",       "Loaded with guac, sour cream, and pico",         1.50),
    ("gourmet",     "Gourmet",      "Elevated with Oaxacan cheese and crema",         2.00),
    ("smothered",   "Smothered",    "Smothered in salsa verde",                       1.00),
    ("crispy",      "Crispy",       "Extra thin and extra crispy",                    0.50),
    ("premium",     "Premium",      "Premium Oaxacan cheese blend",                   2.00),
    ("signature",   "Signature",    "Chef's signature quesadilla",                    1.50),
    ("seasonal",    "Seasonal",     "Rotating seasonal filling",                      1.25),
]

QUESADILLA_BASE_PRICE = 12.99
QUESADILLA_MODIFIERS = [
    {"id": "add_guac",       "name": "Add guacamole", "price": 1.50},
    {"id": "add_sour_cream", "name": "Add sour cream","price": 0.50},
    {"id": "no_onion",       "name": "No onion",      "price": 0.00},
]

# ── 8 Nacho Styles ────────────────────────────────────────────────────────────
NACHO_STYLES = [
    ("street",    "Street",    "Minimalist street-style: protein, onion, cilantro",  0.00),
    ("supreme",   "Supreme",   "All toppings: queso, guac, sour cream, jalapeños",   3.50),
    ("loaded",    "Loaded",    "Fully loaded with every available topping",           4.00),
    ("gourmet",   "Gourmet",   "Elevated with truffle queso and micro herbs",         4.50),
    ("classic",   "Classic",   "Classic cheese, jalapeños, and sour cream",           1.50),
    ("spicy",     "Spicy",     "Loaded with house habanero and jalapeños",            1.50),
    ("chipotle",  "Chipotle",  "Chipotle queso and smoked protein",                   2.00),
    ("seasonal",  "Seasonal",  "Rotating seasonal toppings",                          2.00),
]

NACHO_BASE_PRICE = 12.49
NACHO_MODIFIERS = [
    {"id": "no_jalapeno",  "name": "No jalapeños",  "price": 0.00},
    {"id": "extra_cheese", "name": "Extra cheese",  "price": 1.50},
    {"id": "add_guac",     "name": "Add guacamole", "price": 1.50},
]

# ── 7 Torta Styles ────────────────────────────────────────────────────────────
TORTA_STYLES = [
    ("clasica",   "Clásica",      "Traditional telera roll with beans and avocado",   0.00),
    ("ahogada",   "Ahogada",      "Drowned in spicy tomato-chile broth",               0.50),
    ("cubana",    "Cubana",       "The everything torta with multiple proteins",        2.00),
    ("milanesa",  "Milanesa",     "Breaded and fried protein on telera roll",           1.00),
    ("cemita",    "Cemita",       "Sesame seed cemita with avocado and chipotles",      0.75),
    ("gourmet",   "Gourmet",      "Elevated with artisan bread and premium toppings",   2.00),
    ("premium",   "Premium",      "Premium ingredients on house-baked bread",           2.50),
]

TORTA_BASE_PRICE = 11.49
TORTA_MODIFIERS = [
    {"id": "add_cheese",  "name": "Add cheese",   "price": 0.75},
    {"id": "no_jalapeno", "name": "No jalapeños", "price": 0.00},
    {"id": "add_avocado", "name": "Add avocado",  "price": 1.00},
]

# ── 5 Tostada Styles ──────────────────────────────────────────────────────────
TOSTADA_STYLES = [
    ("classic",   "Classic",    "Refried beans, lettuce, sour cream, and salsa",      0.00),
    ("supreme",   "Supreme",    "Fully loaded with all toppings",                      1.50),
    ("loaded",    "Loaded",     "Extra everything",                                     2.00),
    ("gourmet",   "Gourmet",    "Elevated with micro herbs and crema fresca",           1.75),
    ("street",    "Street-Style","Minimal: protein, onion, cilantro, lime",           -0.50),
]

TOSTADA_BASE_PRICE = 5.49
TOSTADA_MODIFIERS = [
    {"id": "add_guac",  "name": "Add guacamole", "price": 1.50},
    {"id": "add_cheese","name": "Add cheese",    "price": 0.75},
]

# ── 8 Enchilada Sauce Styles ──────────────────────────────────────────────────
ENCHILADA_STYLES = [
    ("rojas",          "Rojas",           "Classic red chile enchiladas",               0.00),
    ("verdes",         "Verdes",          "Tomatillo green sauce enchiladas",            0.00),
    ("mole",           "en Mole",         "Oaxacan black mole enchiladas",               1.00),
    ("suizas",         "Suizas",          "Tomatillo cream sauce enchiladas",            0.50),
    ("chipotle",       "Chipotle",        "Smoky chipotle sauce enchiladas",             0.50),
    ("pipian",         "Pipián",          "Pumpkin seed mole enchiladas",                0.75),
    ("adobo",          "Adobo",           "Smoky adobo chile enchiladas",                0.50),
    ("manchamanteles", "Manchamanteles",  "Fruity mole with plantain and pineapple",     1.00),
]

ENCHILADA_BASE_PRICE = 13.99
ENCHILADA_MODIFIERS = [
    {"id": "extra_cheese", "name": "Extra cheese",  "price": 1.00},
    {"id": "add_sour_cream","name": "Add sour cream","price": 0.50},
]

# ── 4 Flauta Styles ───────────────────────────────────────────────────────────
FLAUTA_STYLES = [
    ("classic",   "Classic Fried",    "Rolled and deep-fried until crispy",             0.00),
    ("baked",     "Baked",            "Oven-baked for a lighter crispy exterior",       -0.50),
    ("doradas",   "Doradas",          "Golden-fried with rendered lard",                 0.50),
    ("suizas",    "Suizas",           "Topped with tomatillo cream and cheese",          0.75),
]

FLAUTA_BASE_PRICE = 10.99
FLAUTA_MODIFIERS = [
    {"id": "add_guac",       "name": "Add guacamole", "price": 1.50},
    {"id": "add_sour_cream", "name": "Add sour cream","price": 0.50},
]

# ── 5 Tamale Styles ───────────────────────────────────────────────────────────
TAMALE_STYLES = [
    ("traditional", "Traditional",    "Corn masa steamed in corn husks",                0.00),
    ("oaxacan",     "Oaxacan",        "Banana leaf-wrapped Oaxacan tamale",              0.50),
    ("rojos",       "Rojos",          "Red chile masa steamed tamale",                   0.25),
    ("verdes",      "Verdes",         "Tomatillo green masa steamed tamale",             0.25),
    ("sweet",       "Sweet",          "Sweet corn masa with raisins and cinnamon",      -0.25),
]

TAMALE_BASE_PRICE = 4.99
TAMALE_MODIFIERS = [
    {"id": "extra_salsa",    "name": "Extra salsa",    "price": 0.50},
    {"id": "add_sour_cream", "name": "Add sour cream", "price": 0.50},
]

# ── 4 Mulita Styles ───────────────────────────────────────────────────────────
MULITA_STYLES = [
    ("classic",  "Classic",  "Two tortillas with cheese melted between",                0.00),
    ("dorada",   "Dorada",   "Crispy pan-fried double tortilla with melted cheese",      0.50),
    ("loaded",   "Loaded",   "Fully loaded mulita with guac and crema",                  1.00),
    ("premium",  "Premium",  "Premium cheese blend and house-made tortillas",            1.25),
]

MULITA_BASE_PRICE = 6.99
MULITA_MODIFIERS = [
    {"id": "no_onion",   "name": "No onion",  "price": 0.00},
    {"id": "add_cheese", "name": "Extra cheese","price": 0.75},
]

# ── 4 Sope Styles ────────────────────────────────────────────────────────────
SOPE_STYLES = [
    ("classic",  "Classic",  "Thick masa cake with pinched edges, fried and topped",    0.00),
    ("loaded",   "Loaded",   "Fully loaded with all traditional toppings",               0.75),
    ("gourmet",  "Gourmet",  "Elevated with heirloom toppings and crema fresca",         1.25),
    ("giant",    "Giant",    "Extra-large sope, feeds 2–3",                              2.00),
]

SOPE_BASE_PRICE = 7.49
SOPE_MODIFIERS = [
    {"id": "add_guac",       "name": "Add guacamole", "price": 1.50},
    {"id": "add_sour_cream", "name": "Add sour cream","price": 0.50},
]

# ── 3 Gordita Styles ──────────────────────────────────────────────────────────
GORDITA_STYLES = [
    ("classic", "Classic", "Thick stuffed corn cake, split and filled",                  0.00),
    ("dorada",  "Dorada",  "Fried until golden, extra crispy shell",                     0.50),
    ("loaded",  "Loaded",  "Stuffed with double filling and all toppings",                1.00),
]

GORDITA_BASE_PRICE = 6.99
GORDITA_MODIFIERS = [
    {"id": "add_cheese",     "name": "Add cheese",     "price": 0.75},
    {"id": "add_sour_cream", "name": "Add sour cream", "price": 0.50},
]

# ── 5 Breakfast Taco Styles ───────────────────────────────────────────────────
BREAKFAST_TACO_STYLES = [
    ("huevos_rancheros", "Huevos Rancheros Style", "Over-easy eggs, ranchero sauce",     0.25),
    ("machaca",          "Machaca Style",           "With dried shredded beef and eggs",  0.50),
    ("chorizo_egg",      "Chorizo & Egg",           "Scrambled eggs with the protein",    0.00),
    ("migas",            "Migas Style",             "Crispy tortilla chips scrambled in", 0.25),
    ("papas",            "Con Papas",               "Seasoned breakfast potatoes added",  0.00),
]

BREAKFAST_TACO_BASE_PRICE = 4.49

# ── Antojito Styles ───────────────────────────────────────────────────────────
ANTOJITO_STYLES = [
    ("street",   "Street",   "Classic street-style antojito preparation",                0.00),
    ("loaded",   "Loaded",   "Fully loaded version of the antojito",                     0.75),
    ("gourmet",  "Gourmet",  "Elevated gourmet take on the classic",                     1.25),
]

ANTOJITO_BASE_PRICE = 5.99

# ── Plato Styles ──────────────────────────────────────────────────────────────
PLATO_STYLES = [
    ("tradicional",  "Tradicional",   "Traditional plate: protein, rice, beans, tortillas", 0.00),
    ("norteno",      "Norteño",       "Northern-style plate with flour tortillas",           0.00),
    ("oaxacan",      "Oaxacan",       "Oaxacan plate with black beans and tlayuda",          1.00),
    ("yucatecan",    "Yucatecan",     "Yucatecan plate with habanero and pickled onion",     0.75),
    ("coastal",      "Coastal",       "Coastal plate with rice and seasonal vegetables",     0.50),
]

PLATO_BASE_PRICE = 16.99
PLATO_MODIFIERS = [
    {"id": "add_guac",       "name": "Add guacamole",  "price": 1.50},
    {"id": "add_sour_cream", "name": "Add sour cream", "price": 0.50},
    {"id": "extra_meat",     "name": "Extra meat",     "price": 3.00},
]


def _make_tags(protein_id: str, protein_label: str, category: str, style_label: str, size: str = "") -> list[str]:
    tags = [protein_id, protein_label.lower(), category]
    if size:
        tags.append(size.lower())
    words = style_label.lower().split()
    tags.extend(w for w in words if len(w) > 3)
    return list(dict.fromkeys(tags))  # deduplicate preserving order


def generate_items() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set(BASE_IDS)

    def add(item: dict) -> None:
        if item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)

    # ── TACOS: 30 proteins × 50 styles × 3 sizes ─────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sprep, sdelta in TACO_STYLES:
            for szid, szlabel, szdelta in TACO_SIZES:
                item_id = f"taco_{pid}_{sid}_{szid}"
                price = round(TACO_BASE_PRICE + psurcharge + sdelta + szdelta, 2)
                price = max(2.99, price)
                name_parts = [plabel, slabel, "Taco"]
                if szlabel:
                    name_parts = [f"({szlabel})", plabel, slabel, "Taco"]
                name = " ".join(name_parts)
                desc = f"{sprep} with {pdesc}."
                if szlabel:
                    size_desc = {"Mini": "half-size portion", "Large": "extra-large portion"}
                    desc += f" {size_desc.get(szlabel, '')}".rstrip()
                add({
                    "id": item_id,
                    "category": "tacos",
                    "name": name,
                    "description": desc.strip(),
                    "price": price,
                    "available": True,
                    "options": TACO_OPTIONS,
                    "modifiers": TACO_MODIFIERS,
                    "dietary_tags": ptags,
                    "tags": _make_tags(pid, plabel, "taco", slabel, szlabel),
                })

    # ── BURRITOS: 30 proteins × 20 styles × 2 sizes ──────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in BURRITO_STYLES:
            for szid, szlabel, szdelta in BURRITO_SIZES:
                item_id = f"burrito_{pid}_{sid}_{szid}"
                price = round(BURRITO_BASE_PRICE + psurcharge + sdelta + szdelta, 2)
                price = max(9.99, price)
                name_parts = [plabel, slabel, "Burrito"]
                if szlabel:
                    name_parts.append(f"({szlabel})")
                name = " ".join(name_parts)
                add({
                    "id": item_id,
                    "category": "burritos",
                    "name": name,
                    "description": f"{sdesc} with {pdesc}, rice, beans, and salsa.",
                    "price": price,
                    "available": True,
                    "options": BURRITO_OPTIONS,
                    "modifiers": BURRITO_MODIFIERS,
                    "dietary_tags": ptags,
                    "tags": _make_tags(pid, plabel, "burrito", slabel, szlabel),
                })

    # ── BOWLS: 30 proteins × 12 styles × 2 sizes ─────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sbase, sdelta in BOWL_STYLES:
            for szid, szlabel, szdelta in BOWL_SIZES:
                item_id = f"bowl_{pid}_{sid}_{szid}"
                price = round(BOWL_BASE_PRICE + psurcharge + sdelta + szdelta, 2)
                price = max(8.99, price)
                name_parts = [plabel, slabel, "Bowl"]
                if szlabel:
                    name_parts.append(f"({szlabel})")
                name = " ".join(name_parts)
                add({
                    "id": item_id,
                    "category": "bowls",
                    "name": name,
                    "description": f"{pdesc} over {sbase} with pico de gallo.",
                    "price": price,
                    "available": True,
                    "options": BOWL_OPTIONS,
                    "modifiers": BOWL_MODIFIERS,
                    "dietary_tags": ptags,
                    "tags": _make_tags(pid, plabel, "bowl", slabel, szlabel),
                })

    # ── QUESADILLAS: 30 proteins × 10 styles ─────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in QUESADILLA_STYLES:
            item_id = f"quesadilla_{pid}_{sid}"
            price = round(QUESADILLA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "quesadillas",
                "name": f"{plabel} {slabel} Quesadilla",
                "description": f"{sdesc}. Filled with {pdesc}.",
                "price": price,
                "available": True,
                "options": {"salsa": {"choices": ["mild", "medium", "hot", "habanero"], "default": "medium", "required": False}},
                "modifiers": QUESADILLA_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "quesadilla", slabel),
            })

    # ── NACHOS: 30 proteins × 8 styles ───────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in NACHO_STYLES:
            item_id = f"nachos_{pid}_{sid}"
            price = round(NACHO_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "nachos",
                "name": f"{slabel} Nachos with {plabel}",
                "description": sdesc,
                "price": price,
                "available": True,
                "options": {},
                "modifiers": NACHO_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "nachos", slabel),
            })

    # ── TORTAS: 30 proteins × 7 styles ───────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in TORTA_STYLES:
            item_id = f"torta_{pid}_{sid}"
            price = round(TORTA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "tortas",
                "name": f"{plabel} Torta {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {"salsa": {"choices": ["mild", "medium", "hot", "habanero"], "default": "medium", "required": False}},
                "modifiers": TORTA_MODIFIERS,
                "dietary_tags": ptags + ["contains_gluten"],
                "tags": _make_tags(pid, plabel, "torta", slabel),
            })

    # ── TOSTADAS: 30 proteins × 5 styles ─────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in TOSTADA_STYLES:
            item_id = f"tostada_{pid}_{sid}"
            price = round(TOSTADA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "tostadas",
                "name": f"{plabel} Tostada {slabel}",
                "description": f"Crispy flat tortilla with {pdesc}. {sdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": TOSTADA_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "tostada", slabel),
            })

    # ── ENCHILADAS: 30 proteins × 8 sauces ───────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in ENCHILADA_STYLES:
            item_id = f"enchiladas_{pid}_{sid}"
            price = round(ENCHILADA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "enchiladas",
                "name": f"Enchiladas {slabel} with {plabel}",
                "description": f"{sdesc} with {pdesc}. Served with rice and beans.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": ENCHILADA_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "enchiladas", slabel),
            })

    # ── FLAUTAS: 30 proteins × 4 styles ──────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in FLAUTA_STYLES:
            item_id = f"flauta_{pid}_{sid}"
            price = round(FLAUTA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "flautas",
                "name": f"{plabel} Flauta {slabel}",
                "description": f"{sdesc} filled with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": FLAUTA_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "flauta", slabel),
            })

    # ── TAMALES: 30 proteins × 5 styles ──────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in TAMALE_STYLES:
            item_id = f"tamale_{pid}_{sid}"
            price = round(TAMALE_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "tamales",
                "name": f"{slabel} Tamale with {plabel}",
                "description": f"{sdesc} filled with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": TAMALE_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "tamale", slabel),
            })

    # ── MULITAS: 30 proteins × 4 styles ──────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in MULITA_STYLES:
            item_id = f"mulita_{pid}_{sid}"
            price = round(MULITA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "mulitas",
                "name": f"{plabel} Mulita {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": MULITA_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "mulita", slabel),
            })

    # ── SOPES: 30 proteins × 4 styles ────────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in SOPE_STYLES:
            item_id = f"sope_{pid}_{sid}"
            price = round(SOPE_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "sopes",
                "name": f"{plabel} Sope {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": SOPE_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "sope", slabel),
            })

    # ── GORDITAS: 30 proteins × 3 styles ─────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in GORDITA_STYLES:
            item_id = f"gordita_{pid}_{sid}"
            price = round(GORDITA_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "gorditas",
                "name": f"{plabel} Gordita {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": GORDITA_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "gordita", slabel),
            })

    # ── PLATOS: 30 proteins × 5 styles ───────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in PLATO_STYLES:
            item_id = f"plato_{pid}_{sid}"
            price = round(PLATO_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "platos",
                "name": f"{plabel} Plato {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": PLATO_MODIFIERS,
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "plato", slabel),
            })

    # ── BREAKFAST TACOS: 30 proteins × 5 styles ──────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in BREAKFAST_TACO_STYLES:
            item_id = f"breakfast_taco_{pid}_{sid}"
            price = round(BREAKFAST_TACO_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "breakfast",
                "name": f"Breakfast Taco {slabel} with {plabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {"salsa": {"choices": ["mild", "medium", "hot", "habanero"], "default": "mild", "required": False}},
                "modifiers": [
                    {"id": "add_cheese",  "name": "Add cheese",  "price": 0.75},
                    {"id": "no_onion",    "name": "No onion",    "price": 0.00},
                    {"id": "no_cilantro", "name": "No cilantro", "price": 0.00},
                ],
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "breakfast", slabel),
            })

    # ── ANTOJITOS: 30 proteins × 3 styles ────────────────────────────────────
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS:
        for sid, slabel, sdesc, sdelta in ANTOJITO_STYLES:
            item_id = f"antojito_{pid}_{sid}"
            price = round(ANTOJITO_BASE_PRICE + psurcharge + sdelta, 2)
            add({
                "id": item_id,
                "category": "antojitos",
                "name": f"{plabel} Antojito {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price,
                "available": True,
                "options": {},
                "modifiers": [{"id": "add_guac", "name": "Add guacamole", "price": 1.50}],
                "dietary_tags": ptags,
                "tags": _make_tags(pid, plabel, "antojito", slabel),
            })

    # ── DRINKS ────────────────────────────────────────────────────────────────
    aguas = [
        ("agua_jamaica",    "Agua de Jamaica",      "Hibiscus flower agua fresca.",          3.99, ["vegan", "vegetarian"]),
        ("agua_tamarindo",  "Agua de Tamarindo",    "Tamarind agua fresca.",                  3.99, ["vegan", "vegetarian"]),
        ("agua_pepino",     "Agua de Pepino",       "Cucumber-lime agua fresca.",             3.99, ["vegan", "vegetarian"]),
        ("agua_sandia",     "Agua de Sandía",       "Watermelon agua fresca.",               3.99, ["vegan", "vegetarian"]),
        ("agua_fresa",      "Agua de Fresa",        "Strawberry agua fresca.",               3.99, ["vegan", "vegetarian"]),
        ("agua_mango",      "Agua de Mango",        "Mango agua fresca with tajín.",          3.99, ["vegan", "vegetarian"]),
        ("agua_pina",       "Agua de Piña",         "Pineapple agua fresca.",                3.99, ["vegan", "vegetarian"]),
        ("agua_limon",      "Agua de Limón",        "Fresh-squeezed limeade.",               3.49, ["vegan", "vegetarian"]),
        ("agua_guanabana",  "Agua de Guanábana",    "Soursop agua fresca.",                   4.29, ["vegan", "vegetarian"]),
        ("agua_coco",       "Agua de Coco",         "Fresh coconut water with lime.",         4.49, ["vegan", "vegetarian"]),
        ("agua_melon",      "Agua de Melón",        "Cantaloupe agua fresca.",               3.99, ["vegan", "vegetarian"]),
        ("agua_mora",       "Agua de Mora",         "Blackberry agua fresca.",               3.99, ["vegan", "vegetarian"]),
        ("agua_maracuya",   "Agua de Maracuyá",     "Passion fruit agua fresca.",             4.29, ["vegan", "vegetarian"]),
        ("agua_guayaba",    "Agua de Guayaba",      "Guava agua fresca.",                    3.99, ["vegan", "vegetarian"]),
        ("agua_ciruela",    "Agua de Ciruela",      "Plum agua fresca.",                     3.99, ["vegan", "vegetarian"]),
        ("agua_naranja",    "Agua de Naranja",      "Fresh orange agua fresca.",             3.99, ["vegan", "vegetarian"]),
        ("agua_zanahoria",  "Agua de Zanahoria",    "Carrot and orange agua fresca.",        3.99, ["vegan", "vegetarian"]),
        ("agua_papaya",     "Agua de Papaya",       "Papaya and lime agua fresca.",          3.99, ["vegan", "vegetarian"]),
        ("agua_chia",       "Agua de Chía",         "Chia seed lime agua fresca.",           4.49, ["vegan", "vegetarian"]),
        ("agua_pepino_menta","Agua Pepino Menta",   "Cucumber-mint agua fresca.",            3.99, ["vegan", "vegetarian"]),
    ]
    beers = [
        ("beer_corona",    "Corona Extra",       "Classic Mexican lager. 355ml.",            4.99, ["vegan", "contains_alcohol"]),
        ("beer_modelo",    "Modelo Especial",    "Rich full-flavored pilsner. 355ml.",        4.99, ["vegan", "contains_alcohol"]),
        ("beer_pacifico",  "Pacifico",           "Light crisp lager. 355ml.",                4.99, ["vegan", "contains_alcohol"]),
        ("beer_tecate",    "Tecate",             "Easy-drinking lager with lime.",            4.49, ["vegan", "contains_alcohol"]),
        ("beer_dos_equis", "Dos Equis",          "Pale lager. 355ml.",                       4.99, ["vegan", "contains_alcohol"]),
        ("beer_negra",     "Negra Modelo",       "Dark Munich-style lager. 355ml.",          5.49, ["vegan", "contains_alcohol"]),
        ("beer_victoria",  "Victoria",           "Vienna-style amber lager. 355ml.",         4.99, ["vegan", "contains_alcohol"]),
        ("beer_sol",       "Sol",                "Light refreshing Mexican lager.",           4.49, ["vegan", "contains_alcohol"]),
        ("beer_bohemia",   "Bohemia",            "Premium Czech-style dark lager.",          5.49, ["vegan", "contains_alcohol"]),
        ("beer_carta",     "Carta Blanca",       "Classic light Mexican lager.",             4.49, ["vegan", "contains_alcohol"]),
        ("beer_superior",  "Superior",           "Crisp balanced pilsner.",                  4.49, ["vegan", "contains_alcohol"]),
        ("beer_indio",     "Indio",              "Dark amber Munich-style. 355ml.",          4.99, ["vegan", "contains_alcohol"]),
        ("beer_montejo",   "Montejo",            "Yucatecan-style pale lager.",              4.99, ["vegan", "contains_alcohol"]),
        ("beer_cucapah",   "Cucapá Honey",       "Honey craft lager from Baja.",             5.99, ["vegan", "contains_alcohol"]),
        ("beer_minerva",   "Minerva IPA",        "Mexican craft IPA, Guadalajara.",          6.49, ["vegan", "contains_alcohol"]),
    ]
    margaritas = [
        ("marg_classic_rocks",     "Classic Margarita (Rocks)",      "House tequila, triple sec, fresh lime.",        10.99, ["vegan", "contains_alcohol"]),
        ("marg_classic_frozen",    "Classic Margarita (Frozen)",     "House tequila, triple sec, fresh lime, blended.",10.99, ["vegan", "contains_alcohol"]),
        ("marg_mango_rocks",       "Mango Margarita (Rocks)",        "Tequila, mango purée, lime.",                   11.99, ["vegan", "contains_alcohol"]),
        ("marg_mango_frozen",      "Mango Margarita (Frozen)",       "Tequila, mango purée, lime, blended.",          11.99, ["vegan", "contains_alcohol"]),
        ("marg_strawberry_rocks",  "Strawberry Margarita (Rocks)",   "Tequila, fresh strawberry, lime.",              11.99, ["vegan", "contains_alcohol"]),
        ("marg_strawberry_frozen", "Strawberry Margarita (Frozen)",  "Tequila, fresh strawberry, blended.",           11.99, ["vegan", "contains_alcohol"]),
        ("marg_watermelon_rocks",  "Watermelon Margarita (Rocks)",   "Tequila, watermelon, lime.",                    11.99, ["vegan", "contains_alcohol"]),
        ("marg_tamarind_rocks",    "Tamarind Margarita (Rocks)",     "Tequila, tamarind, lime, Tajín rim.",           11.99, ["vegan", "contains_alcohol"]),
        ("marg_spicy_cucumber",    "Spicy Cucumber Margarita",       "Tequila, cucumber, jalapeño, lime.",            12.99, ["vegan", "contains_alcohol"]),
        ("marg_blood_orange",      "Blood Orange Margarita",         "Tequila, blood orange, lime.",                  12.99, ["vegan", "contains_alcohol"]),
        ("marg_guava_rocks",       "Guava Margarita (Rocks)",        "Tequila, guava, lime.",                         11.99, ["vegan", "contains_alcohol"]),
        ("marg_hibiscus",          "Hibiscus Margarita",             "Tequila, jamaica, lime, agave.",                12.49, ["vegan", "contains_alcohol"]),
        ("marg_mezcal_rocks",      "Mezcal Margarita (Rocks)",       "Mezcal, triple sec, fresh lime.",               13.99, ["vegan", "contains_alcohol"]),
        ("marg_mezcal_spicy",      "Spicy Mezcal Margarita",         "Mezcal, habanero, lime.",                       14.99, ["vegan", "contains_alcohol"]),
        ("marg_paloma",            "Paloma Cocktail",                "Tequila, grapefruit, lime, salt.",              11.99, ["vegan", "contains_alcohol"]),
        ("marg_ranch_water",       "Ranch Water",                    "Blanco tequila, lime, Topo Chico.",             10.99, ["vegan", "contains_alcohol"]),
        ("marg_oaxacan_old",       "Oaxacan Old Fashioned",          "Mezcal, tequila, mole bitters.",                14.99, ["vegan", "contains_alcohol"]),
        ("marg_naked_mezcal",      "Naked Mezcal Negroni",           "Mezcal, sweet vermouth, Campari.",              13.99, ["vegan", "contains_alcohol"]),
    ]
    mocktails = [
        ("mock_virgin_marg",    "Virgin Margarita",          "House sour mix, lime, sparkling water.",        6.99, ["vegan", "vegetarian"]),
        ("mock_mango_virgin",   "Virgin Mango Margarita",    "Mango purée, lime, sparkling water.",           6.99, ["vegan", "vegetarian"]),
        ("mock_watermelon_spk", "Watermelon Sparkler",       "Fresh watermelon juice, mint, sparkling.",      6.99, ["vegan", "vegetarian"]),
        ("mock_cucumber_spa",   "Cucumber Spa Water",        "Cucumber, mint, lime sparkling water.",         5.99, ["vegan", "vegetarian"]),
        ("mock_hibiscus_fizz",  "Hibiscus Fizz",             "Jamaica concentrate, lime, soda.",              5.99, ["vegan", "vegetarian"]),
        ("mock_tamarind_sour",  "Tamarind Sour Mocktail",    "Tamarind, lime, Tajín, soda water.",            6.49, ["vegan", "vegetarian"]),
        ("mock_pineapple_jalap","Pineapple Jalapeño Spritz", "Pineapple, jalapeño, lime, sparkling.",         6.99, ["vegan", "vegetarian"]),
        ("mock_mexican_mule",   "Mexican Mule Mocktail",     "Ginger beer, lime, mint.",                      5.99, ["vegan", "vegetarian"]),
    ]
    hot_drinks = [
        ("hot_cafe_olla",     "Café de Olla",           "Traditional spiced coffee with cinnamon.",         3.49, ["vegetarian"]),
        ("hot_black_coffee",  "Black Coffee",            "House drip coffee.",                               2.99, ["vegan", "vegetarian"]),
        ("hot_cafe_leche",    "Café con Leche",          "Espresso with steamed milk.",                      4.49, ["vegetarian", "contains_dairy"]),
        ("hot_abuelita",      "Abuelita Hot Chocolate",  "Traditional Mexican chocolate drink.",             4.49, ["vegetarian", "contains_dairy"]),
        ("hot_champurrado",   "Champurrado",             "Thick masa-based hot chocolate.",                  4.99, ["vegetarian", "contains_dairy"]),
        ("hot_te_manzanilla", "Té de Manzanilla",        "Chamomile herbal tea.",                            2.99, ["vegan", "vegetarian"]),
        ("hot_te_jamaica",    "Té de Jamaica",           "Hot hibiscus tea.",                                2.99, ["vegan", "vegetarian"]),
        ("hot_atole",         "Atole de Vainilla",       "Warm corn-based drink with vanilla.",             4.49, ["vegetarian"]),
        ("hot_espresso",      "Espresso",                "Double shot of house espresso.",                   3.49, ["vegan", "vegetarian"]),
        ("hot_cortado",       "Cortado",                 "Equal parts espresso and steamed milk.",           3.99, ["vegetarian", "contains_dairy"]),
    ]
    soft_drinks = [
        ("soda_coke",         "Coca-Cola",          "Classic Coca-Cola.",                                3.49, ["vegan", "vegetarian"]),
        ("soda_diet_coke",    "Diet Coke",          "Diet Coca-Cola.",                                   3.49, ["vegan", "vegetarian"]),
        ("soda_sprite",       "Sprite",             "Lemon-lime soda.",                                  3.49, ["vegan", "vegetarian"]),
        ("soda_dr_pepper",    "Dr Pepper",          "Dr Pepper soda.",                                   3.49, ["vegan", "vegetarian"]),
        ("soda_topo_chico",   "Topo Chico",         "Sparkling mineral water.",                          3.99, ["vegan", "vegetarian"]),
        ("soda_jarritos_mand","Jarritos Mandarin",  "Mexican mandarin soda.",                            3.99, ["vegan", "vegetarian"]),
        ("soda_jarritos_lime","Jarritos Lime",      "Mexican lime soda.",                                3.99, ["vegan", "vegetarian"]),
        ("soda_jarritos_grap","Jarritos Grapefruit","Mexican grapefruit soda.",                          3.99, ["vegan", "vegetarian"]),
        ("soda_jarritos_str", "Jarritos Strawberry","Mexican strawberry soda.",                          3.99, ["vegan", "vegetarian"]),
        ("soda_sidral",       "Sidral Mundet",      "Mexican apple soda.",                               3.99, ["vegan", "vegetarian"]),
        ("soda_sangria",      "Sangría Señorial",   "Non-alcoholic sangría-style soda.",                 3.99, ["vegan", "vegetarian"]),
        ("soda_peñafiel",     "Peñafiel Mineral",   "Natural mineral water.",                            3.49, ["vegan", "vegetarian"]),
    ]
    wines = [
        ("wine_house_red",    "House Red Wine",     "House red, 6oz pour.",                              8.99, ["vegan", "contains_alcohol"]),
        ("wine_house_white",  "House White Wine",   "House white, 6oz pour.",                            8.99, ["vegan", "contains_alcohol"]),
        ("wine_house_rose",   "House Rosé",         "House rosé, 6oz pour.",                             8.99, ["vegan", "contains_alcohol"]),
        ("wine_malbec",       "Malbec",             "Argentine Malbec, 6oz pour.",                      10.99, ["vegan", "contains_alcohol"]),
        ("wine_cab_sauv",     "Cabernet Sauvignon", "California Cabernet, 6oz pour.",                   10.99, ["vegan", "contains_alcohol"]),
        ("wine_pinot_grigio", "Pinot Grigio",       "Italian Pinot Grigio, 6oz pour.",                  10.99, ["vegan", "contains_alcohol"]),
        ("wine_sauvignon_b",  "Sauvignon Blanc",    "New Zealand Sauvignon Blanc, 6oz.",                10.99, ["vegan", "contains_alcohol"]),
        ("wine_prosecco",     "Prosecco",           "Italian sparkling prosecco, 5oz.",                 10.99, ["vegan", "contains_alcohol"]),
        ("wine_sangria_red",  "Red Sangria (Pitcher)","House red sangria with seasonal fruit.",          24.99, ["vegan", "contains_alcohol"]),
        ("wine_sangria_wht",  "White Sangria (Pitcher)","House white sangria with citrus.",              24.99, ["vegan", "contains_alcohol"]),
    ]
    micheladas = [
        ("miche_clasica",    "Michelada Clásica",      "Beer, lime, hot sauce, Tajín rim.",             7.99, ["vegan", "contains_alcohol"]),
        ("miche_clamato",    "Michelada con Clamato",  "Beer and Clamato with spices.",                  8.49, ["shellfish", "contains_alcohol"]),
        ("miche_campechana", "Campechana Michelada",   "Mixed seafood cocktail-style michelada.",        9.49, ["shellfish", "contains_alcohol"]),
        ("miche_verde",      "Michelada Verde",        "Beer, tomatillo, serrano, lime.",               8.49, ["vegan", "contains_alcohol"]),
        ("miche_negra",      "Michelada Negra",        "Dark beer, lime, extra spicy.",                  8.49, ["vegan", "contains_alcohol"]),
    ]

    for drink_list in [aguas, beers, margaritas, mocktails, hot_drinks, soft_drinks, wines, micheladas]:
        for entry in drink_list:
            did, dname, ddesc, dprice, dtags = entry
            item_id = f"drink_{did}"
            add({
                "id": item_id,
                "category": "drinks",
                "name": dname,
                "description": ddesc,
                "price": dprice,
                "available": True,
                "options": {},
                "modifiers": [],
                "dietary_tags": dtags,
                "tags": [did.replace("_", " "), "drink", "beverage"],
            })

    # ── SIDES ─────────────────────────────────────────────────────────────────
    sides = [
        ("side_beans_pinto",       "Pinto Beans",              "Slow-cooked pinto beans.",                           3.49, ["vegan", "vegetarian"]),
        ("side_guacamole",         "Guacamole (4oz)",          "Fresh-made guacamole, 4oz cup.",                     3.49, ["vegan", "vegetarian"]),
        ("side_guacamole_lg",      "Guacamole (8oz)",          "Fresh-made guacamole, 8oz.",                         6.99, ["vegan", "vegetarian"]),
        ("side_sour_cream",        "Sour Cream",               "2oz cup of house sour cream.",                       1.49, ["vegetarian", "contains_dairy"]),
        ("side_pico",              "Pico de Gallo",            "Fresh tomato, onion, jalapeño, cilantro.",           1.99, ["vegan", "vegetarian"]),
        ("side_jalapenos",         "Pickled Jalapeños",        "Pickled jalapeño slices.",                           1.49, ["vegan", "vegetarian"]),
        ("side_consomé",           "Cup of Consomé",           "Birria beef broth for dipping.",                     2.49, ["beef"]),
        ("side_salsa_verde",       "Salsa Verde",              "Roasted tomatillo and serrano salsa.",               1.99, ["vegan", "vegetarian"]),
        ("side_salsa_roja",        "Salsa Roja",               "Roasted tomato and chile de árbol.",                 1.99, ["vegan", "vegetarian"]),
        ("side_salsa_habanero",    "Habanero Salsa",           "House habanero salsa. Very hot.",                    1.99, ["vegan", "vegetarian"]),
        ("side_totopos",           "Totopos (Plain)",          "House tortilla chips, no salsa.",                    2.49, ["vegan", "vegetarian"]),
        ("side_queso_fundido",     "Queso Fundido",            "Melted Oaxacan cheese with chorizo.",                6.99, ["contains_dairy", "pork"]),
        ("side_elote_cup",         "Elote en Vaso",            "Mexican street corn in a cup with cotija.",          4.49, ["vegetarian", "contains_dairy"]),
        ("side_pozole",            "Pozole Rojo",              "Hominy and pork in red chile broth.",                8.99, ["pork"]),
        ("side_caldo_pollo",       "Caldo de Pollo",           "Chicken broth with vegetables.",                     6.49, ["chicken"]),
        ("side_rice_brown",        "Brown Rice",               "Steamed house brown rice.",                          2.99, ["vegan", "vegetarian"]),
        ("side_cauliflower",       "Cauliflower Rice",         "Seasoned cauliflower rice.",                         3.49, ["vegan", "vegetarian"]),
        ("side_street_corn",       "Street Corn Elote",        "Grilled corn on the cob with cotija and crema.",     4.99, ["vegetarian", "contains_dairy"]),
        ("side_nopales",           "Grilled Nopales",          "Grilled cactus paddle with lime.",                   3.99, ["vegan", "vegetarian"]),
        ("side_plantains",         "Fried Sweet Plantains",    "Golden-fried sweet plantains.",                      4.49, ["vegan", "vegetarian"]),
        ("side_jicama",            "Jícama Sticks",            "Fresh jícama with chili and lime.",                  3.49, ["vegan", "vegetarian"]),
        ("side_queso_dip",         "Queso Dip (Cup)",          "Warm house queso dip with chips.",                   4.99, ["vegetarian", "contains_dairy"]),
        ("side_bean_dip",          "Bean Dip",                 "Warm refried bean dip with chips.",                  3.99, ["vegetarian"]),
        ("side_roasted_veggies",   "Roasted Vegetables",       "Seasonal roasted vegetables with lime.",             4.99, ["vegan", "vegetarian"]),
        ("side_tortillas_corn",    "Corn Tortillas (3pc)",     "Fresh house-made corn tortillas.",                   1.99, ["vegan", "vegetarian"]),
        ("side_tortillas_flour",   "Flour Tortillas (3pc)",    "House flour tortillas.",                             1.99, ["vegetarian"]),
        ("side_pickled_onion",     "Pickled Red Onion",        "House-pickled red onion.",                           1.49, ["vegan", "vegetarian"]),
        ("side_chipotle_crema",    "Chipotle Crema",           "Smoky chipotle sour cream.",                         1.49, ["vegetarian", "contains_dairy"]),
        ("side_avocado_slices",    "Avocado Slices",           "Fresh avocado with lime and salt.",                  3.99, ["vegan", "vegetarian"]),
        ("side_extra_salsa",       "Extra Salsa (Choice)",     "Extra house salsa, your choice.",                    0.99, ["vegan", "vegetarian"]),
        ("side_chips_lg",          "Large Chips & Salsa",      "Extra-large chips with house salsa.",                5.99, ["vegan", "vegetarian"]),
        ("side_chips_3salsas",     "Chips & 3 Salsas",         "Chips with roja, verde, and habanero.",              7.99, ["vegan", "vegetarian"]),
        ("side_chile_relleno",     "Chile Relleno",            "Roasted poblano stuffed with cheese, tomato sauce.", 8.99, ["vegetarian", "contains_dairy"]),
        ("side_tamale_single",     "Single Tamale",            "One house tamale (your choice of protein).",         4.99, ["varies"]),
        ("side_esquites",          "Esquites",                 "Warm corn kernels with mayo, cotija, chili.",        3.99, ["vegetarian", "contains_dairy"]),
    ]
    for sid, sname, sdesc, sprice, stags in sides:
        add({"id": sid, "category": "sides", "name": sname, "description": sdesc, "price": sprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": stags, "tags": ["side"]})

    # ── SOUPS ────────────────────────────────────────────────────────────────
    soups = [
        ("soup_pozole_rojo",  "Pozole Rojo",              "Hominy and pork in red chile broth.",               12.99, ["pork"]),
        ("soup_pozole_verde", "Pozole Verde",             "Hominy and chicken in tomatillo broth.",            12.99, ["chicken"]),
        ("soup_pozole_blanco","Pozole Blanco",            "Hominy in clear pork broth, Jalisco style.",        12.99, ["pork"]),
        ("soup_birria_sopa",  "Birria Soup",              "Slow-braised beef in rich red consomé.",            13.99, ["beef"]),
        ("soup_caldo_res",    "Caldo de Res",             "Mexican beef and vegetable soup.",                  12.99, ["beef"]),
        ("soup_caldo_pollo",  "Caldo de Pollo",           "Chicken and vegetable soup.",                       11.99, ["chicken"]),
        ("soup_sopa_lima",    "Sopa de Lima",             "Yucatecan citrus soup with chicken.",               11.99, ["chicken"]),
        ("soup_sopa_tortilla","Sopa de Tortilla",         "Crispy tortilla strips in tomato chile broth.",     10.99, ["vegetarian"]),
        ("soup_menudo",       "Menudo",                   "Traditional beef tripe and hominy soup.",           13.99, ["beef"]),
        ("soup_tlalpeño",     "Caldo Tlalpeño",           "Chicken and chickpea soup with chipotle.",          11.99, ["chicken"]),
        ("soup_black_bean",   "Black Bean Soup",          "Creamy black bean soup with crema.",                8.99, ["vegetarian"]),
        ("soup_gazpacho_mex", "Gazpacho Mexicano",        "Cold fruit salad with lime and Tajín.",             6.99, ["vegan", "vegetarian"]),
    ]
    for sid, sname, sdesc, sprice, stags in soups:
        add({"id": sid, "category": "soups", "name": sname, "description": sdesc, "price": sprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": stags, "tags": ["soup"]})

    # ── SALADS ────────────────────────────────────────────────────────────────
    salad_proteins = [p for p in PROTEINS if "shellfish" not in p[3] and "beef" in p[3] or "chicken" in p[3] or "vegetarian" in p[3]]
    salad_styles = [
        ("taco",       "Taco Salad",      "Crispy shell, romaine, pico, cheese, sour cream",   1.00),
        ("ensalada",   "Ensalada",        "Mixed greens, pepitas, radish, crema dressing",      0.00),
        ("nopales",    "Nopal Salad",     "Grilled nopales, tomato, onion, queso fresco",       0.50),
        ("jicama",     "Jícama Slaw",     "Jícama, carrot, lime dressing, fresh herbs",         0.00),
    ]
    for pid, plabel, pdesc, ptags, psurcharge in PROTEINS[:15]:
        for sid, slabel, sdesc, sdelta in salad_styles:
            item_id = f"salad_{pid}_{sid}"
            price = round(12.49 + psurcharge + sdelta, 2)
            add({
                "id": item_id, "category": "salads",
                "name": f"{plabel} {slabel}",
                "description": f"{sdesc} with {pdesc}.",
                "price": price, "available": True, "options": {},
                "modifiers": [{"id": "add_guac", "name": "Add guacamole", "price": 1.50}],
                "dietary_tags": ptags, "tags": _make_tags(pid, plabel, "salad", slabel),
            })

    # ── DESSERTS ─────────────────────────────────────────────────────────────
    desserts = [
        ("dessert_tres_leches",     "Tres Leches Cake",      "Sponge cake soaked in three milks.",         6.49, ["vegetarian", "contains_dairy", "contains_gluten"]),
        ("dessert_bunuelos",        "Buñuelos",              "Crispy fried dough with cinnamon sugar.",    4.49, ["vegetarian", "contains_gluten"]),
        ("dessert_sopapillas",      "Sopapillas (4pc)",      "Pillowy fried pastry with honey.",           4.99, ["vegetarian", "contains_gluten"]),
        ("dessert_paleta_choco",    "Chocolate Paleta",      "House-made chocolate popsicle.",             3.49, ["vegetarian", "contains_dairy"]),
        ("dessert_paleta_mango",    "Mango-Chili Paleta",    "Fresh mango with Tajín.",                    3.49, ["vegan", "vegetarian"]),
        ("dessert_paleta_horchata", "Horchata Paleta",       "Creamy horchata popsicle.",                  3.49, ["vegetarian", "contains_dairy"]),
        ("dessert_paleta_jamaica",  "Jamaica Paleta",        "Hibiscus flower popsicle.",                  3.49, ["vegan", "vegetarian"]),
        ("dessert_empanada_apple",  "Apple Empanada",        "Flaky pastry filled with spiced apple.",    3.99, ["vegetarian", "contains_gluten"]),
        ("dessert_empanada_cajeta", "Cajeta Empanada",       "Flaky pastry filled with caramel.",         3.99, ["vegetarian", "contains_gluten", "contains_dairy"]),
        ("dessert_arroz_leche",     "Arroz con Leche",       "Mexican rice pudding.",                      4.49, ["vegetarian", "contains_dairy"]),
        ("dessert_creme_bru",       "Mexican Crème Brûlée",  "Vanilla crème brûlée with cinnamon.",       7.49, ["vegetarian", "contains_dairy"]),
        ("dessert_capirotada",      "Capirotada",            "Traditional Mexican bread pudding.",         5.99, ["vegetarian", "contains_gluten", "contains_dairy"]),
        ("dessert_conchas",         "Conchas (2pc)",         "Traditional Mexican sweet bread.",           3.99, ["vegetarian", "contains_gluten"]),
        ("dessert_paleta_coconut",  "Coconut Paleta",        "Fresh coconut cream popsicle.",             3.49, ["vegetarian", "contains_dairy"]),
        ("dessert_paleta_watermel", "Watermelon Paleta",     "Fresh watermelon popsicle.",                3.49, ["vegan", "vegetarian"]),
        ("dessert_helado_vainilla", "Helado de Vainilla",    "Mexican vanilla ice cream, 2 scoops.",      4.99, ["vegetarian", "contains_dairy"]),
        ("dessert_helado_choco",    "Helado de Chocolate",   "Rich chocolate ice cream, 2 scoops.",       4.99, ["vegetarian", "contains_dairy"]),
        ("dessert_paleta_tamarind", "Tamarind-Chili Paleta", "Sweet-spicy tamarind popsicle.",            3.49, ["vegan", "vegetarian"]),
        ("dessert_xangos",          "Xangos",                "Deep-fried cheesecake chimichanga.",        7.99, ["vegetarian", "contains_gluten", "contains_dairy"]),
        ("dessert_nieve_limon",     "Nieve de Limón",        "Lime sherbet, house-made.",                  4.49, ["vegetarian"]),
    ]
    for did, dname, ddesc, dprice, dtags in desserts:
        add({"id": did, "category": "desserts", "name": dname, "description": ddesc, "price": dprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": dtags, "tags": ["dessert", "sweet"]})

    # ── BREAKFAST (non-taco) ─────────────────────────────────────────────────
    breakfast = [
        ("bfast_chilaquiles_rojos",  "Chilaquiles Rojos",     "Crispy tortilla chips in red salsa, topped with fried egg, queso fresco.", 11.99, ["vegetarian"]),
        ("bfast_chilaquiles_verdes", "Chilaquiles Verdes",    "Crispy chips in tomatillo salsa, crema, and queso fresco.", 11.99, ["vegetarian"]),
        ("bfast_huevos_rancheros",   "Huevos Rancheros",      "Two fried eggs on corn tortillas with ranchero sauce.", 10.99, ["vegetarian"]),
        ("bfast_huevos_motulenos",   "Huevos Motuleños",      "Yucatecan fried eggs on tortillas with black beans and plantains.", 12.99, ["vegetarian"]),
        ("bfast_huevos_divorciados", "Huevos Divorciados",    "Two eggs — one ranchero style, one verde style.", 11.49, ["vegetarian"]),
        ("bfast_machaca_huevos",     "Machaca con Huevo",     "Dried shredded beef scrambled with eggs, onion, and peppers.", 13.99, ["beef"]),
        ("bfast_migas",              "Migas",                 "Scrambled eggs with crispy tortilla strips, cheese, and peppers.", 10.99, ["vegetarian"]),
        ("bfast_molletes",           "Molletes",              "Bolillo with refried beans, cheese, and pico de gallo.",  9.99, ["vegetarian", "contains_gluten"]),
        ("bfast_burrito_desayuno",   "Burrito de Desayuno",   "Large breakfast burrito with eggs, potato, cheese, and salsa.", 11.49, ["vegetarian"]),
        ("bfast_tamale_desayuno",    "Tamale de Desayuno",    "Morning tamale with egg and cheese.",               9.99, ["vegetarian"]),
        ("bfast_atole_bowl",         "Atole Bowl",            "Warm atole with pan dulce on the side.",            8.99, ["vegetarian", "contains_dairy"]),
        ("bfast_menudo_weekend",     "Weekend Menudo",        "Traditional weekend menudo with hominy.",           13.99, ["beef"]),
        ("bfast_torta_desayuno",     "Torta de Huevo",        "Egg and cheese torta on telera roll.",              9.99, ["vegetarian", "contains_gluten", "contains_dairy"]),
        ("bfast_enchiladas_rojas",   "Breakfast Enchiladas",  "Egg-filled enchiladas in red sauce.",              12.99, ["vegetarian"]),
        ("bfast_sopas_sopa",         "Sopa de Fideos",        "Toasted vermicelli pasta in tomato broth.",         7.99, ["vegan", "vegetarian"]),
    ]
    for bid, bname, bdesc, bprice, btags in breakfast:
        add({"id": bid, "category": "breakfast", "name": bname, "description": bdesc, "price": bprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": btags, "tags": ["breakfast"]})

    # ── KIDS MENU ─────────────────────────────────────────────────────────────
    kids = [
        ("kids_taco_pollo",    "Kids Chicken Taco",    "One grilled chicken taco with mild salsa and rice.", 5.99, ["chicken"]),
        ("kids_taco_cheese",   "Kids Cheese Taco",     "One cheese taco on a corn tortilla.",               4.49, ["vegetarian", "contains_dairy"]),
        ("kids_quesadilla",    "Kids Quesadilla",      "Small cheese quesadilla with sour cream.",           5.99, ["vegetarian", "contains_dairy"]),
        ("kids_burrito_pollo", "Kids Chicken Burrito", "Small chicken burrito with rice and beans.",         7.49, ["chicken"]),
        ("kids_chips_guac",    "Kids Chips & Guac",    "Small chips with guacamole.",                        3.49, ["vegan", "vegetarian"]),
        ("kids_bowl",          "Kids Bowl",            "Small bowl with rice, beans, and mild chicken.",     7.99, ["chicken"]),
        ("kids_hotdog",        "Kids Taco Dog",        "Kid-friendly taco on a flour tortilla, mild.",       5.49, ["chicken"]),
        ("kids_nachos",        "Kids Nachos",          "Small nachos with mild cheese and chicken.",         6.49, ["chicken", "contains_dairy"]),
        ("kids_soda",          "Kids Soda",            "Small soda, your choice.",                           1.99, ["vegan", "vegetarian"]),
        ("kids_milk",          "Kids Milk",            "Cold 8oz milk.",                                    1.99, ["vegetarian", "contains_dairy"]),
    ]
    for kid, kname, kdesc, kprice, ktags in kids:
        add({"id": kid, "category": "kids", "name": kname, "description": kdesc, "price": kprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": ktags, "tags": ["kids", "children"]})

    # ── COMBOS ────────────────────────────────────────────────────────────────
    combos = [
        ("combo_2tacos_drink",      "2 Tacos + Drink",         "Any 2 tacos with a fountain drink.",              12.99, []),
        ("combo_3tacos_drink",      "3 Tacos + Drink",         "Any 3 tacos with a fountain drink.",              15.99, []),
        ("combo_4tacos",            "4 Taco Plate",            "4 tacos with rice and beans.",                    18.99, []),
        ("combo_5tacos",            "5 Taco Plate",            "5 tacos with rice and beans.",                    22.99, []),
        ("combo_burrito_drink",     "Burrito + Drink",         "Any burrito with a fountain drink.",              15.99, []),
        ("combo_bowl_drink",        "Bowl + Drink",            "Any bowl with a fountain drink.",                 15.49, []),
        ("combo_2enchil_rice",      "2 Enchiladas + Rice",     "2 enchiladas with rice and beans.",               16.99, []),
        ("combo_3enchil_rice",      "3 Enchiladas + Rice",     "3 enchiladas with rice and beans.",               20.99, []),
        ("combo_burrito_taco",      "Burrito + Taco Combo",    "One burrito and one taco with drink.",            17.99, []),
        ("combo_quesadilla_drink",  "Quesadilla + Drink",      "Quesadilla with fountain drink.",                 15.99, []),
        ("combo_family_tacos_12",   "Family Taco Pack (12)",   "12 tacos with rice, beans, and salsas.",         44.99, []),
        ("combo_family_tacos_24",   "Family Taco Pack (24)",   "24 tacos for large gatherings.",                 84.99, []),
        ("combo_family_burritos_4", "Family Burrito Pack (4)", "4 burritos with rice and beans.",                49.99, []),
        ("combo_family_burritos_8", "Family Burrito Pack (8)", "8 burritos for parties.",                        89.99, []),
        ("combo_family_bowls_4",    "Family Bowl Pack (4)",    "4 bowls with all toppings.",                     52.99, []),
        ("combo_catering_sml",      "Catering Pack Small",     "Feeds 10–15: 30 tacos, rice, beans, salsas.",   149.99, []),
        ("combo_catering_med",      "Catering Pack Medium",    "Feeds 20–25: 60 tacos, rice, beans, salsas.",   279.99, []),
        ("combo_catering_lrg",      "Catering Pack Large",     "Feeds 40–50: 120 tacos, sides, salsas.",        549.99, []),
        ("combo_taco_tuesday",      "Taco Tuesday Deal",       "3 tacos + horchata for a special price.",        13.99, []),
        ("combo_lunch_special",     "Lunch Special",           "Bowl or burrito + drink + chips.",               16.49, []),
    ]
    for cid, cname, cdesc, cprice, ctags in combos:
        add({"id": cid, "category": "combos", "name": cname, "description": cdesc, "price": cprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": ctags, "tags": ["combo", "deal"]})

    # ── SPECIALTY PLATTERS ────────────────────────────────────────────────────
    platters = [
        ("platter_taco_asst",       "Assorted Taco Platter",        "10 assorted tacos, chef's selection.",         38.99, []),
        ("platter_burrito_asst",    "Assorted Burrito Platter",     "5 assorted burritos, chef's selection.",        59.99, []),
        ("platter_quesadilla_asst", "Quesadilla Platter",           "6 quesadilla wedges with dipping sauces.",     36.99, []),
        ("platter_nacho_bar",       "Nacho Bar",                    "Full nacho bar setup, serves 6–8.",             44.99, []),
        ("platter_taco_birria_10",  "Birria Taco Platter (10pc)",   "10 birria tacos with consomé.",                 49.99, ["beef"]),
        ("platter_enchilada_trio",  "Enchilada Trio Platter",       "3 enchiladas (roja, verde, mole) with rice.",  24.99, []),
        ("platter_antojito_asst",   "Antojito Assortment",          "Sopes, gorditas, tostadas, and mulitas.",      42.99, []),
        ("platter_brunch",          "Weekend Brunch Platter",       "Chilaquiles, huevos rancheros, fruit, drinks.", 34.99, []),
        ("platter_dessert",         "Dessert Platter",              "Assorted desserts for the table.",              24.99, ["vegetarian"]),
        ("platter_chips_salsa_bar", "Chips & Salsa Bar",            "Unlimited chips with 5 house salsas.",         19.99, ["vegan", "vegetarian"]),
    ]
    for pid, pname, pdesc, pprice, ptags in platters:
        add({"id": pid, "category": "platters", "name": pname, "description": pdesc, "price": pprice,
             "available": True, "options": {}, "modifiers": [], "dietary_tags": ptags, "tags": ["platter", "group", "sharing"]})

    return items


def main():
    items = generate_items()
    out_path = ROOT / "data" / "menu_expanded.json"
    with open(out_path, "w") as f:
        json.dump(items, f, indent=2)

    categories: dict[str, int] = {}
    for item in items:
        categories[item["category"]] = categories.get(item["category"], 0) + 1

    print(f"Generated {len(items):,} items → {out_path}")
    print()
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        bar = "█" * (count // 100)
        print(f"  {cat:20s}: {count:5,}  {bar}")
    print()
    assert len(items) >= 10_000, f"Expected ≥10,000 items, got {len(items)}"
    print("✓ 10,000+ item target met")


if __name__ == "__main__":
    main()
