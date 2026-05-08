from typing import Dict, List


def build_football_driver_templates() -> Dict[str, Dict[str, List[dict]]]:
    return {
        "football_team_win": {
            "yes": [{"id": x, "label": x.replace('_',' ').title(), "description": "", "impact": "high" if x in {"strong_lineup","recent_form"} else "medium", "data_needed": []} for x in ["strong_lineup","opponent_weakness","recent_form","home_advantage","motivation","market_odds_movement"]],
            "no": [{"id": x, "label": x.replace('_',' ').title(), "description": "", "impact": "very_high" if x=="injuries_suspensions" else "high" if x in {"draw_risk","rotation","strong_opponent"} else "medium", "data_needed": []} for x in ["draw_risk","injuries_suspensions","rotation","fixture_congestion","strong_opponent","low_motivation"]],
            "required": ["confirmed opponent","starting lineups","injury/suspension report","recent form","home/away context","motivation/rotation context","odds movement before kickoff"],
        },
        "football_tournament_advancement": {
            "yes": [{"id": x, "label": x.replace('_',' ').title(), "description": "", "impact": "high", "data_needed": []} for x in ["current_tie_state","opponent_strength","bracket_path","lineup_availability","home_away_factor","club_motivation"]],
            "no": [{"id": x, "label": x.replace('_',' ').title(), "description": "", "impact": "high", "data_needed": []} for x in ["difficult_opponent","negative_first_leg_score","key_absences","fixture_congestion","away_disadvantage","poor_form"]],
            "required": ["tournament stage","opponent and tie format","first-leg score / current tie state","aggregate / extra time / penalty rules","injuries/suspensions","home/away factor","market line movement"],
        },
        "football_tournament_winner_group": {
            "yes": [{"id": x, "label": x.replace('_',' ').title(), "description": "", "impact": "high", "data_needed": []} for x in ["group_teams_remaining","combined_outright_odds","bracket_path","team_strength_depth","injuries_form","favorable_draw"]],
            "no": [{"id": x, "label": x.replace('_',' ').title(), "description": "", "impact": "high", "data_needed": []} for x in ["strong_non_group_favorites","group_teams_eliminate_each_other","hard_bracket","key_injuries","low_number_of_group_teams_remaining","weak_outright_odds"]],
            "required": ["list of target-group teams still in competition","individual outright winner odds by club","tournament bracket/path","risk of target-group teams eliminating each other","strongest non-group competitors","injury/form status of target-group teams","current stage"],
        },
    }
