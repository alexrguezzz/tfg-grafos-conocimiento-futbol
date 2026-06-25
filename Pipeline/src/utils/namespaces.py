from rdflib import Namespace


BASE = "http://example.org/TFG_SoccerData/"

EX = Namespace(BASE)
CLASS = Namespace(f"{BASE}class/")
PROP = Namespace(f"{BASE}property/")
RESOURCE = Namespace(f"{BASE}resource/")


# Classes
COMPETITION = CLASS.Competition
SEASON = CLASS.Season
TEAM = CLASS.Team
MATCH = CLASS.Match
TEAM_MATCH_PARTICIPATION = CLASS.TeamMatchParticipation
TEAM_COMPETITION_SEASON = CLASS.TeamCompetitionSeason
ELO_RECORD = CLASS.EloRecord
PLAYER = CLASS.Player
EVENT = CLASS.Event
PLAYER_MATCH_PARTICIPATION = CLASS.PlayerMatchParticipation
PLAYER_COMPETITION_SEASON_STATS = CLASS.PlayerCompetitionSeasonStats
STADIUM = CLASS.Stadium
WEATHER_OBSERVATION = CLASS.WeatherObservation


# Properties
HAS_MATCH = PROP.hasMatch
BELONGS_TO_MATCH = PROP.belongsToMatch
BELONGS_TO_COMPETITION = PROP.belongsToCompetition
BELONGS_TO_SEASON = PROP.belongsToSeason
HAS_TEAM_COMPETITION_SEASON = PROP.hasTeamCompetitionSeason
BELONGS_TO_TEAM_COMPETITION_SEASON = PROP.belongsToTeamCompetitionSeason
HAS_TEAM_MATCH_PARTICIPATION = PROP.hasTeamMatchParticipation
BELONGS_TO_TEAM_MATCH_PARTICIPATION = PROP.belongsToTeamMatchParticipation
HAS_PLAYER_MATCH_PARTICIPATION = PROP.hasPlayerMatchParticipation
HAS_EVENT = PROP.hasEvent
INVOLVES_TEAM_MATCH_PARTICIPATION = PROP.involvesTeamMatchParticipation
INVOLVES_PLAYER_MATCH_PARTICIPATION = PROP.involvesPlayerMatchParticipation
INVOLVES_SECONDARY_PLAYER_MATCH_PARTICIPATION = PROP.involvesSecondaryPlayerMatchParticipation
CORRESPONDS_TO_TEAM = PROP.correspondsToTeam
CORRESPONDS_TO_PLAYER = PROP.correspondsToPlayer
HAS_ELO_RECORD = PROP.hasEloRecord
HAS_PLAYER_COMPETITION_SEASON_STATS = PROP.hasPlayerCompetitionSeasonStats
PLAYED_AT_STADIUM = PROP.playedAtStadium
HAS_WEATHER_OBSERVATION = PROP.hasWeatherObservation

NAME = PROP.name
MATCH_NAME = PROP.name
WEEK = PROP.matchDay
MATCH_DATE = PROP.date
MATCH_DATETIME = PROP.dateTime
MATCH_STATUS = PROP.matchStatus
HOME_SCORE = PROP.homeScore
AWAY_SCORE = PROP.awayScore
FTR = PROP.finalResult
HTHG = PROP.halftimeHomeScore
HTAG = PROP.halftimeAwayScore
HTR = PROP.halftimeResult
ATTENDANCE = PROP.attendance
ID_SOFASCORE = PROP.idSofascore
ID_UNDERSTAT = PROP.idUnderstat
SEASON_ID_UNDERSTAT = PROP.id_understat
ID_WHOSCORED = PROP.idWhoscored
TEAM_CODE = PROP.teamCode
CITY = PROP.city
COUNTRY = PROP.country
LATITUDE = PROP.latitude
LONGITUDE = PROP.longitude
ID_WIKIDATA = PROP.idWikidata
ID_OSM = PROP.idOsm
WEATHER_DATE_TIME = PROP.dateTime
TEMPERATURE = PROP.temperature
PRECIPITATION = PROP.precipitation
RAIN = PROP.rain
WIND_SPEED = PROP.windSpeed
HUMIDITY = PROP.humidity

IS_HOME = PROP.isHome

POSITION = PROP.position
MP = PROP.matchesPlayed
PLAYER_MATCHES = PROP.matches
W = PROP.wins
D = PROP.draws
L = PROP.losses
GF = PROP.goalsFor
GA = PROP.goalsAgainst
GD = PROP.goalDifference
PTS = PROP.points

DATE_FROM = PROP.dateFrom
DATE_TO = PROP.dateTo
RANK = PROP.rank
LEVEL = PROP.level
ELO = PROP.elo

PARTICIPATION_STATUS = PROP.participationStatus
IS_CAPTAIN = PROP.isCaptain
SUB_IN = PROP.subIn
SUB_OUT = PROP.subOut
APPEARANCES = PROP.appearances
FOULS_COMMITTED = PROP.foulsCommitted
FOULS_SUFFERED = PROP.foulsSuffered
OWN_GOALS = PROP.ownGoals
RED_CARDS = PROP.redCards
YELLOW_CARDS = PROP.yellowCards
GOALS_CONCEDED = PROP.goalsConceded
SAVES = PROP.saves
GOAL_ASSISTS = PROP.goalAssists
PLAYER_ASSISTS = PROP.assists
SHOTS_ON_TARGET = PROP.shotsOnTarget
TOTAL_GOALS = PROP.totalGoals
PLAYER_GOALS = PROP.goals
TOTAL_SHOTS = PROP.totalShots
PLAYER_SHOTS = PROP.shots
OFFSIDES = PROP.offsides
MINUTES = PROP.minutes
XG = PROP.xg
XG_CHAIN = PROP.xg_chain
XG_BUILDUP = PROP.xg_buildup
SEASON_XG_CHAIN = PROP.xgChain
SEASON_XG_BUILDUP = PROP.xgBuildup
XA = PROP.xa
KEY_PASSES = PROP.keyPasses
REASON = PROP.reason
STATUS = PROP.status
NP_GOALS = PROP.nonPenaltyGoals
NP_XG = PROP.nonPenaltyXg

POSSESSION_PCT = PROP.possessionPct
WON_CORNERS = PROP.wonCorners
PENALTY_KICK_GOALS = PROP.penaltyKickGoals
PENALTY_KICK_SHOTS = PROP.penaltyKickShots
ACCURATE_PASSES = PROP.accuratePasses
TOTAL_PASSES = PROP.totalPasses
ACCURATE_CROSSES = PROP.accurateCrosses
TOTAL_CROSSES = PROP.totalCrosses
TOTAL_LONG_BALLS = PROP.totalLongBalls
ACCURATE_LONG_BALLS = PROP.accurateLongBalls
BLOCKED_SHOTS = PROP.blockedShots
EFFECTIVE_TACKLES = PROP.effectiveTackles
TOTAL_TACKLES = PROP.totalTackles
INTERCEPTIONS = PROP.interceptions
TOTAL_CLEARANCE = PROP.totalClearance
NP_XG_DIFFERENCE = PROP.nonPenaltyXgDifference
PPDA = PROP.ppda
DEEP_COMPLETIONS = PROP.deepCompletions

EVENT_PERIOD = PROP.period
EVENT_MINUTE = PROP.minute
EVENT_SECOND = PROP.second
EVENT_EXPANDED_MINUTE = PROP.expandedMinute
EVENT_TYPE = PROP.type
OUTCOME_TYPE = PROP.outcomeType
X_COORD = PROP.x
Y_COORD = PROP.y
END_X = PROP.endX
END_Y = PROP.endY
GOAL_MOUTH_Y = PROP.goalMouthY
GOAL_MOUTH_Z = PROP.goalMouthZ
BLOCKED_X = PROP.blockedX
BLOCKED_Y = PROP.blockedY
QUALIFIERS = PROP.qualifiers
IS_TOUCH = PROP.isTouch
IS_SHOT = PROP.isShot
IS_GOAL = PROP.isGoal
CARD_TYPE = PROP.cardType
IS_RELATED_TO_EVENT = PROP.isRelatedToEvent


def competition_uri(competition_id: str):
    return RESOURCE[f"competition/{competition_id}"]


def season_uri(season_id: str):
    return RESOURCE[f"season/{season_id}"]


def team_uri(team_id: str):
    return RESOURCE[f"team/{team_id}"]


def match_uri(match_id: str):
    return RESOURCE[f"match/{match_id}"]


def team_match_participation_uri(participation_id: str):
    return RESOURCE[f"team_match_participation/{participation_id}"]


def team_competition_season_uri(tcs_id: str):
    return RESOURCE[f"team_competition_season/{tcs_id}"]


def elo_record_uri(elo_id: str):
    return RESOURCE[f"elo_record/{elo_id}"]


def player_uri(player_id: str):
    return RESOURCE[f"player/{player_id}"]


def event_uri(event_id: str):
    return RESOURCE[f"event/{event_id}"]


def player_match_participation_uri(participation_id: str):
    return RESOURCE[f"player_match_participation/{participation_id}"]


def player_competition_season_stats_uri(stats_id: str):
    return RESOURCE[f"player_competition_season_stats/{stats_id}"]


def stadium_uri(stadium_id: str):
    return RESOURCE[f"stadium/{stadium_id}"]


def weather_observation_uri(weather_id: str):
    return RESOURCE[f"weather_observation/{weather_id}"]
