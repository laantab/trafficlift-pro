import logging
import requests

logger = logging.getLogger("ProductControlAgent")
logging.basicConfig(level=logging.INFO)

class ProductControlAgent:
    @staticmethod
    def audit_product(product: dict) -> dict:
        product_id = product.get("id", "dynamic-product")
        required_fields = ["id", "name", "category", "image_url", "url", "angle", "pin_title", "pin_description", "hashtags"]
        for field in required_fields:
            if not product.get(field):
                raise ValueError(f"Product Control Violation: Missing mandatory field '{field}'.")
        
        category = product["category"].lower()
        name = product["name"].lower()
        
        category_keywords = {
            "cleaning": ["scrub", "clean", "brush", "mop", "vacuum", "wipe", "cleaner", "grout", "ultrasonic"],
            "tech": ["charger", "station", "cable", "wireless", "magsafe", "led", "phone", "hub", "dock"],
            "pet": ["dog", "cat", "pet", "bed", "pup", "leash", "grooming", "calming"],
            "home decor": ["lamp", "sunset", "projection", "light", "decor", "aesthetic", "ambient"]
        }
        
        matched_category = next((k for k in category_keywords if k in category), None)
        if matched_category:
            valid_terms = category_keywords[matched_category]
            if not any(term in name for term in valid_terms):
                raise ValueError(f"Product Control Violation: Semantic mismatch between category '{category}' and product name '{name}'.")

        image_url = product.get("image_url")
        if not image_url or not image_url.startswith("http"):
            raise ValueError(f"Product Control Violation: Invalid image URL format '{image_url}'.")

        try:
            head_res = requests.head(image_url, timeout=3, allow_redirects=True)
            if head_res.status_code >= 400:
                logger.warning(f"[Agent Warning] Image URL returned status {head_res.status_code}: {image_url}")
        except Exception as e:
            logger.warning(f"[Agent Notice] Image reachability check failed: {str(e)}")

        logger.info(f"[Product Control Agent] Product '{product_id}' successfully audited and cleared.")
        return product
