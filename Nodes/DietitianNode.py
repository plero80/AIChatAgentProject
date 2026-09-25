from Services.DietitianService import DietitianService


from Graph.GraphState import GraphState


class DietitianNode:

    def __init__(self, dietitian_service: DietitianService):
        self.dietitian_service = dietitian_service

    async def __call__(self, state: GraphState) -> dict:
        
        dietitian_url = self.dietitian_service.get_dietitian_url()
        text = f"Here is the link to the dietitian form: {dietitian_url}"

        return {
            "result": {
                "mode": "dietitian_escort",
                "text": text,
            }
        }
