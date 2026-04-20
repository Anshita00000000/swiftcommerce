# Brand Research Report
Generated: 2026-03-09
Method: Selenium (Chrome, headful, undetected mode)

---

=== Luminox ===
platform: Shopify
listing_url_confirmed: https://luminox.com/collections/all-watches
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: a[href*='/products/']
product_url_pattern: /products/{slug}
pagination_type: url_param
pagination_detail: ?page=N, max page link seen: 2
approximate_total_products: 62
sku_location: data_attribute
sku_attribute_name: data-sku
sku_example: XS.3251.CB.VOL
men_url: https://luminox.com/collections/mens-watches
women_url: https://luminox.com/collections/womens-watches

-- AVAILABILITY STATUS --
status_on_listing_page: yes
listing_status_selector: .product-badge
listing_status_values_seen: ['NEW', 'LIMITED EDITION']
status_on_product_page: yes
product_status_selector: button[name='add']
product_status_values_seen: ['Add to Bag', 'SOLD OUT']
status_notes: 

---

=== G-Shock ===
platform: Unknown
listing_url_confirmed: not_found
blocks_selenium: yes

-- URL COLLECTION --
product_link_selector: not_found
product_url_pattern: unknown
pagination_type: unknown
pagination_detail: 
approximate_total_products: unknown
sku_location: not_found
sku_attribute_name: not_found
sku_example: not_found
men_url: https://www.casio.com/us/watches/gshock/
women_url: not_found

-- AVAILABILITY STATUS --
status_on_listing_page: no
listing_status_selector: not_applicable
listing_status_values_seen: []
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: All listing URLs failed to load or were blocked

---

=== Hamilton ===
platform: Custom
listing_url_confirmed: https://www.hamiltonwatch.com/en-us/filter-by.html
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: a[href*='.html']
product_url_pattern: /en-us/filter-by/{slug}.html
pagination_type: next_button
pagination_detail: rel=next / .pagination__next
approximate_total_products: 165
sku_location: data_attribute
sku_attribute_name: data-sku
sku_example: H71636330
men_url: https://www.hamiltonwatch.com/en-us/filter-by/mens-watches.html
women_url: https://www.hamiltonwatch.com/en-us/filter-by/womens-watches.html

-- AVAILABILITY STATUS --
status_on_listing_page: yes
listing_status_selector: [class*='unavailable']
listing_status_values_seen: ['Available soon']
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: 

---

=== Tissot ===
platform: Custom
listing_url_confirmed: https://www.tissotwatches.com/en-us/collection.html
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: a[href*='/en-us/']
product_url_pattern: /en-us/men/main-collections/{slug}.html
pagination_type: url_param
pagination_detail: ?page=N, max page link seen: 14
approximate_total_products: 238
sku_location: data_attribute
sku_attribute_name: data-id
sku_example: C56UCV4K7EFNSJPVSUMG
men_url: https://www.tissotwatches.com/en-us/men.html
women_url: https://www.tissotwatches.com/en-us/women.html

-- AVAILABILITY STATUS --
status_on_listing_page: yes
listing_status_selector: [class*='add-to-cart']
listing_status_values_seen: []
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: 

---

=== Rado ===
platform: Custom
listing_url_confirmed: https://www.rado.com/en_us/watches/all-watches.html
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: a[href*='/en_us/']
product_url_pattern: /en_us/{slug}.html
pagination_type: unknown
pagination_detail: no pagination indicators found
approximate_total_products: 102
sku_location: data_attribute
sku_attribute_name: data-id
sku_example: C4FMU67M9G8R5RJ0LIO0
men_url: https://www.rado.com/en_us/watches/all-watches/men-watches.html
women_url: https://www.rado.com/en_us/watches/all-watches/women-watches.html

-- AVAILABILITY STATUS --
status_on_listing_page: no
listing_status_selector: not_applicable
listing_status_values_seen: []
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: 

---

=== Victorinox ===
platform: Custom
listing_url_confirmed: https://www.victorinox.com/en-US/Products/Watches/c/TP_AllProducts/
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: a[href*='/Products/']
product_url_pattern: /en-US/Products/Swiss-Army-Knife%E2%84%A2-and-Tools/c/{slug}
pagination_type: unknown
pagination_detail: no pagination indicators found
approximate_total_products: 92
sku_location: not_found
sku_attribute_name: not_found
sku_example: not_found
men_url: https://www.victorinox.com/en-US/Products/Watches/Men%27s-Watches/c/TP-mens-watches/
women_url: https://www.victorinox.com/en-US/Products/Watches/Women's-Watches/c/TP_WomenWatches/

-- AVAILABILITY STATUS --
status_on_listing_page: yes
listing_status_selector: [class*='add-to-cart']
listing_status_values_seen: []
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: 

---

=== Movado ===
platform: Custom
listing_url_confirmed: https://www.movado.com/us/en/shop-watches/shop-all-watches
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: a[href*='/us/en/']
product_url_pattern: /us/en/shop-watches/{slug}
pagination_type: unknown
pagination_detail: no pagination indicators found
approximate_total_products: 111
sku_location: not_found
sku_attribute_name: not_found
sku_example: not_found
men_url: https://www.movado.com/us/en/mens-designs
women_url: https://www.movado.com/us/en/womens-designs

-- AVAILABILITY STATUS --
status_on_listing_page: yes
listing_status_selector: [class*='add-to-cart']
listing_status_values_seen: []
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: 

---

=== Michele ===
platform: Custom
listing_url_confirmed: https://www.michele.com/en-us/watches/
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: not_found
product_url_pattern: unknown
pagination_type: unknown
pagination_detail: no pagination indicators found
approximate_total_products: unknown
sku_location: not_found
sku_attribute_name: not_found
sku_example: not_found
men_url: not_applicable
women_url: not_applicable

-- AVAILABILITY STATUS --
status_on_listing_page: no
listing_status_selector: not_applicable
listing_status_values_seen: []
status_on_product_page: no
product_status_selector: not_found
product_status_values_seen: []
status_notes: 

---

=== Seiko ===
platform: Shopify
listing_url_confirmed: https://seikousa.com/collections/all
blocks_selenium: no

-- URL COLLECTION --
product_link_selector: #product-grid a[href*='/products/']
product_url_pattern: /collections/all/products/{slug}
pagination_type: next_button
pagination_detail: load-more button present
approximate_total_products: 518
sku_location: data_attribute
sku_attribute_name: data-sku
sku_example: SRPL61
men_url: https://seikousa.com/collections/mens
women_url: https://seikousa.com/collections/womens

-- AVAILABILITY STATUS --
status_on_listing_page: yes
listing_status_selector: [class*='sold-out']
listing_status_values_seen: ['Regular price\n$795.00', 'SOLD OUT', 'Regular price\n$450.00']
status_on_product_page: yes
product_status_selector: button[type='submit'][class*='add']
product_status_values_seen: ['DOWNLOAD MANUAL', 'ADD TO CART']
status_notes: 

---
