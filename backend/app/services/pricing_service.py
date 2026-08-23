class PricingService:
    """Applies pricing you enter for a model to the raw token counts Azure reports."""

    @staticmethod
    def estimate_cost(
        prompt_tokens: int,
        cached_tokens: int,
        completion_tokens: int,
        input_price_per_million: float,
        cached_price_per_million: float,
        output_price_per_million: float,
    ) -> float:
        billable_prompt_tokens = max(prompt_tokens - cached_tokens, 0)
        cost = (
            billable_prompt_tokens / 1_000_000 * input_price_per_million
            + cached_tokens / 1_000_000 * cached_price_per_million
            + completion_tokens / 1_000_000 * output_price_per_million
        )
        return round(cost, 6)
