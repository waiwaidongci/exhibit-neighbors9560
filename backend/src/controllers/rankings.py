from ..services import rankings
class RankingsController:
    list=staticmethod(rankings.list_rankings)
    create=staticmethod(rankings.create_ranking)
