from abc import ABC
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks
from peewee import SqliteDatabase, Model, CharField, ForeignKeyField, \
                   IntegerField, BooleanField, DateTimeField

# Constants
STARTING_MONEY = 6900
DATABASE_FILE = "test.sqlite"
DISCORD_TOKEN = "THE OLD TOKEN HAS BEEN REVOKED"
COMMAND_PREFIX = "!b "

# Bet type enumeration
BET_TYPE_NULL = 0
BET_TYPE_STAT = 1
BET_TYPE_TEXT = 2

# Establish database settings
db = SqliteDatabase(DATABASE_FILE, pragmas={
    'journal_mode': 'wal',
    'cache_size': -1 * 64000,  # 64MB
    'foreign_keys': 1,
    'ignore_check_constraints': 0,
    'synchronous': 0})


# ===============
# Database models
# ===============
class BaseModel(Model):
    class Meta:
        database = db


class User(BaseModel):
    uid = IntegerField()
    coins = IntegerField(default=STARTING_MONEY)
    last_beg = DateTimeField(default=datetime.min)


class BaseBet(BaseModel):
    user1 = ForeignKeyField(User)
    user2 = ForeignKeyField(User, null=True)
    amount = IntegerField()
    resolve_time = DateTimeField()
    resolved = BooleanField(default=False)
    checked = BooleanField(default=False)
    bet_type = IntegerField()
    message_id = IntegerField()


class StatBet(BaseModel):
    bet = ForeignKeyField(BaseBet, on_delete="CASCADE", backref="stat_bet")
    stat_class = CharField()
    stat_name = CharField()
    stat_value = IntegerField()


class TextBet(BaseModel):
    bet = ForeignKeyField(BaseBet, on_delete="CASCADE", backref="text_bet")
    wager = CharField()
    user1_outcome = CharField(null=True)
    user2_outcome = CharField(null=True)


db.connect()
db.create_tables([User, BaseBet, StatBet, TextBet])


# ========================
# Betting helper functions
# ========================

async def resolve_bet(bet, winner):
    """Pays out the bet winnings to the winner and DMs both parties"""
    print(f"resolving bet {bet.id}")
    user1 = await bot.fetch_user(bet.user1.uid)

    # Check to make sure the bet was actually accepted
    if(bet.user2):
        user2 = await bot.fetch_user(bet.user2.uid)
        if(winner == bet.user1):
            await user1.send("You won!")
            await user2.send("You lost :(")
        else:
            await user2.send("You won!")
            await user1.send("You lost :(")
        update_query = User.update(coins=User.coins + 2*bet.amount).where(User.uid == winner.uid)
        update_query.execute()
    else:
        update_query = User.update(coins=User.coins + bet.amount).where(User.uid == bet.user1.uid)
        update_query.execute()
        await user1.send("Nobody accepted your bet, better luck next time.")
    bet.resolved = True
    bet.save()


async def check_manual_bet(bet):
    """DMs both parties to ask how the bet went"""
    if(not bet.user2):
        await resolve_bet(bet, bet.user1)
        return
    user1 = await bot.fetch_user(bet.user1.uid)
    user2 = await bot.fetch_user(bet.user2.uid)
    check = (f"Did the following statement come to pass?\n {bet.text_bet.get().wager}\n" +
             f"If it did, please reply \"!b verify {bet.id} true\" otherwise reply " +
             f"\"!b verify {bet.id} false\".")
    await user1.send(check)
    await user2.send(check)
    bet.checked = True
    bet.save()


# ==============================
# Discord Bot Setup/Events/Tasks
# ==============================

# The main bot object
bot = commands.Bot(command_prefix=COMMAND_PREFIX, case_insensitve=True)


@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.CheckFailure):
        await ctx.send('You do not have the correct role for this command.')
    elif isinstance(error, commands.errors.MissingPermissions):
        await ctx.send('You do not have the correct permissions for this command.')
    elif isinstance(error, commands.errors.MissingRequiredArgument):
        await ctx.send("Please supply an argument to the command.")
    elif isinstance(error, commands.errors.TooManyArguments):
        await ctx.send("Too many arguments supplied to the command.")
    else:
        print("unknown command error", error)


@tasks.loop(seconds=5.0)
async def bet_eval_loop():
    """Checks to see which bets have expired and start the manual checking process"""
    expired_bets = BaseBet.select().where(datetime.now() > BaseBet.resolve_time,
                                          BaseBet.checked == False)
    for bet in expired_bets:
        await check_manual_bet(bet)


@bot.event
async def on_raw_reaction_add(payload):
    """Deals with accepting the checkmark indicating someone accepted the bet"""
    try:
        bet = BaseBet.get(message_id=payload.message_id)
    except BaseBet.DoesNotExist:
        return

    if(payload.emoji.name == "✅" and payload.user_id != bot.user.id and bet.user2 is None):
        user, _ = User.get_or_create(uid=payload.user_id)
        if(user.coins < bet.amount):
            channel = bot.get_channel(payload.channel_id)
            await channel.send("Insufficient coins to accept this bet.")
            return
        else:
            update_query = User.update(coins=User.coins - bet.amount).where(User.uid == user.uid)
            update_query.execute()
        bet.user2 = user
        bet.save()

    print(bet)
    return


# ================
# Discord Commands
# ================

@bot.command(name='text_bet')
async def make_bet(ctx, text, amount, duration):
    """Makes a bet"""
    user, _ = User.get_or_create(uid=ctx.message.author.id)

    if(user.coins < int(amount)):
        await ctx.send("Insufficient coins to place this bet.")
        return

    update_query = User.update(coins=User.coins - int(amount)).where(User.uid == user.uid)
    update_query.execute()
    msg = await ctx.send("New Bet: " + text)
    bet = BaseBet.create(user1=user, amount=int(amount),
                         resolve_time=(datetime.now() + timedelta(minutes=int(duration))),
                         bet_type=BET_TYPE_TEXT, message_id=msg.id)
    TextBet.create(bet=bet, wager=text)

    await msg.add_reaction("✅")


@bot.command(name='verify')
async def verify_bet(ctx, bet_id, outcome):
    """Allows a user to manually verify the now hopefully resolved bet"""
    outcome = outcome.lower()
    if(outcome != "true" and outcome != "false"):
        await ctx.send("The second argument to verify must be 'true' or 'false'")
        return
    user, _ = User.get_or_create(uid=ctx.message.author.id)
    try:
        bet = BaseBet.get(BaseBet.id == int(bet_id))
    except BaseBet.DoesNotExist:
        await ctx.send("No such bet exists")
        return

    text_bet = bet.text_bet.get()
    if(user == bet.user1):
        text_bet.user1_outcome = outcome
        text_bet.save()
    elif(user == bet.user2):
        text_bet.user2_outcome = outcome
        text_bet.save()
    else:
        await ctx.send("You don't have permission to verify this bet")
        return

    if(text_bet.user1_outcome and text_bet.user2_outcome and
       text_bet.user1_outcome == text_bet.user2_outcome):
        if(text_bet.user1_outcome == "true"):
            await resolve_bet(bet, bet.user1)
        else:
            await resolve_bet(bet, bet.user2)


@bot.command(name='coins')
async def get_coins(ctx):
    """Tells a user how many coins they have"""
    user, _ = User.get_or_create(uid=ctx.message.author.id)

    await ctx.send(f"You have {user.coins} coins.")


# Kick everything off!
bet_eval_loop.start()
bot.run(DISCORD_TOKEN)
