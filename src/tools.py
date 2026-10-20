import os
import time
import logging
from io import BytesIO
from typing import List, Optional

import requests
from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types
from crewai.tools import tool

load_dotenv()
logging.basicConfig(level=logging.INFO)

# --------------------
# Gemini setup (thay cho Credentials / APIClient / project_id của IBM)
# --------------------
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

VISION_MODEL = "gemini-3.1-flash-lite"  # đổi sang gemini-2.5-flash nếu bị 503
TEXT_MODEL = "gemini-3.5-flash-lite"


def _generate(contents, max_tokens: int, retries: int = 4) -> str:
    """Gọi Gemini, tự retry khi bị 503/429."""
    config = types.GenerateContentConfig(temperature=0, max_output_tokens=max_tokens)
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=VISION_MODEL, contents=contents, config=config
            )
            return (response.text or "").strip()
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt * 3
            logging.warning(f"Gemini lỗi ({e}), thử lại sau {wait}s...")
            time.sleep(wait)


def _load_image(image_input: str):
    """Đọc ảnh từ URL hoặc đường dẫn local, trả về (bytes, mime_type)."""
    if image_input.startswith("http"):
        response = requests.get(image_input, timeout=30)
        response.raise_for_status()
        data = response.content
    else:
        if not os.path.isfile(image_input):
            raise FileNotFoundError(f"No file found at path: {image_input}")
        with open(image_input, "rb") as f:
            data = f.read()

    try:
        fmt = Image.open(BytesIO(data)).format or "JPEG"
        mime = Image.MIME.get(fmt, "image/jpeg")
    except Exception:
        mime = "image/jpeg"
    return data, mime


class ExtractIngredientsTool:
    @tool("Extract ingredients")
    def extract_ingredient(image_input: str):
        """
        Extract ingredients from a food item image.

        :param image_input: The image file path (local) or URL (remote).
        :return: A list of ingredients extracted from the image.
        """
        logging.info("Extracting ingredients from image...")
        data, mime = _load_image(image_input)
        return _generate(
            [
                types.Part.from_bytes(data=data, mime_type=mime),
                "Extract ingredients from the food item image",
            ],
            max_tokens=800,
        )


class FilterIngredientsTool:
    @tool("Filter ingredients")
    def filter_ingredients(raw_ingredients: str) -> List[str]:
        """
        Processes the raw ingredient data and filters out non-food items or noise.

        :param raw_ingredients: Raw ingredients as a string.
        :return: A list of cleaned and relevant ingredients.
        """
        return [
            ingredient.strip().lower()
            for ingredient in raw_ingredients.split(",")
            if ingredient.strip()
        ]


class DietaryFilterTool:
    @tool("Filter based on dietary restrictions")
    def filter_based_on_restrictions(
        ingredients: List[str], dietary_restrictions: Optional[str] = None
    ) -> List[str]:
        """
        Uses an LLM model to filter ingredients based on dietary restrictions.

        :param ingredients: List of ingredients.
        :param dietary_restrictions: Dietary restrictions (e.g., vegan, gluten-free). Defaults to None.
        :return: Filtered list of ingredients that comply with the dietary restrictions.
        """
        if not dietary_restrictions:
            return ingredients

        prompt = f"""
        You are an AI nutritionist specialized in dietary restrictions.
        Given the following list of ingredients: {', '.join(ingredients)},
        and the dietary restriction: {dietary_restrictions},
        remove any ingredient that does not comply with this restriction.
        Return only the compliant ingredients as a comma-separated list with no additional commentary.
        """

        filtered = _generate(prompt, max_tokens=500).lower()
        return [item.strip() for item in filtered.split(",") if item.strip()]


class NutrientAnalysisTool:
    @tool("Analyze nutritional values and calories of the dish from uploaded image")
    def analyze_image(image_input: str):
        """
        Provide a detailed nutrient breakdown and estimate the total calories of all ingredients from the uploaded image.

        :param image_input: The image file path (local) or URL (remote).
        :return: A string with nutrient breakdown (protein, carbs, fat, etc.) and estimated calorie information.
        """
        data, mime = _load_image(image_input)

        assistant_prompt = """
            You are an expert nutritionist. Your task is to analyze the food items displayed in the image and provide a detailed nutritional assessment using the following format:
        1. **Identification**: List each identified food item clearly, one per line.
        2. **Portion Size & Calorie Estimation**: For each identified food item, specify the portion size and provide an estimated number of calories. Use bullet points with the following structure:
        - **[Food Item]**: [Portion Size], [Number of Calories] calories
        Example:
        *   **Salmon**: 6 ounces, 210 calories
        *   **Asparagus**: 3 spears, 25 calories
        3. **Total Calories**: Provide the total number of calories for all food items.
        Example:
        Total Calories: [Number of Calories]
        4. **Nutrient Breakdown**: Include a breakdown of key nutrients such as **Protein**, **Carbohydrates**, **Fats**, **Vitamins**, and **Minerals**. Use bullet points, and for each nutrient provide details about the contribution of each food item.
        Example:
        *   **Protein**: Salmon (35g), Asparagus (3g), Tomatoes (1g) = [Total Protein]
        5. **Health Evaluation**: Evaluate the healthiness of the meal in one paragraph.
        6. **Disclaimer**: Include the following exact text as a disclaimer:
        The nutritional information and calorie estimates provided are approximate and are based on general food data.
        Actual values may vary depending on factors such as portion size, specific ingredients, preparation methods, and individual variations.
        For precise dietary advice or medical guidance, consult a qualified nutritionist or healthcare provider.
        Format your response exactly like the template above to ensure consistency.
        """

        return _generate(
            [types.Part.from_bytes(data=data, mime_type=mime), assistant_prompt],
            max_tokens=2000,
        )