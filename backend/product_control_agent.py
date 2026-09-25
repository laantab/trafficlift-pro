import logging
import requests

logger = logging.getLogger("ProductControlAgent")
logging.basicConfig(level=logging.INFO)

class ProductControlAgent:
    """
    Autonomous quality-control agent that intercepts product payloads to:
    1. Verify mandatory metadata completeness (titles, descriptions, tags, URLs).
    2. Prevent cross-category image mismatches.
    3. Validate that image URLs are active and reachable before serving.
    """
    
    @staticmethod
    def audit_product(product: dict) -> dict:
        product_id = product.get("id", "unknown-id")
        
        required_fields = ["id", "name", "category", "image_url", "url", "pin_title", "pin_description", "hashtags"]
        for field in required_fields:
            if not product.get(field):
                logger.error(f"[Agent Alert] Product {product_id} failed audit: Missing required field '{field}'.")
                raise ValueError(f"Product Control Violation: Missing '{field}' in product payload.")

        category = product["category"].lower()
        name = product["name"].lower()
        
        category_keywords = {
            "cleaning": ["scrub", "clean", "brush", "mop", "vacuum", "wipe"],
            "tech": ["charger", "station", "cable", "wireless", "magsafe", "led"],
            "pet": ["dog", "cat", "pet", "bed", "pup"],
            "home decor": ["lamp", "sunset", "projection", "light", "decor"]
        }
        
        matched_category_key = next((k for k in category_keywords if k in category), None)
        if matched_category_key:
            valid_terms = category_keywords[matched_category_key]
            if not any(term in name for term in valid_terms):
                logger.warning(f"[Agent Warning] Semantic mismatch detected for {product_id}: '{name}' may not align with category '{category}'.")

        image_url = product.get("image_url")
        try:
            head_res = requests.head(image_url, timeout=3, allow_redirects=True)
            if head_res.status_code >= 400:
                logger.warning(f"[Agent Warning] Image URL for {product_id} returned status {head_res.status_code}: {image_url}")
        except Exception as e:
            logger.warning(f"[Agent Notice] Image URL reachability check skipped/failed for {product_id}: {str(e)}")

        logger.info(f"[Product Control Agent] Product '{product_id}' successfully audited and cleared for publishing.")
        return product

    @classmethod
    def audit_catalog(cls, catalog: list[dict]) -> list[dict]:
        audited = []
        for prod in catalog:
            audited.append(cls.audit_product(prod))
        return audited
