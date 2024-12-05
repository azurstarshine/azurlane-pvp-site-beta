from functools import partial
import json

# We only use GitPython for some specific utilities.
# It does not support sparse checkouts and other features we
# use to minimize disk space and downloads.
from git.repo.fun import is_git_dir
from git.util import rmtree as git_rmtree
from invoke import task
from invoke.exceptions import Exit

from pvpdata import GAME_RESOURCES_DIR
from pvpdata import PROJECT_ROOT
from pvpdata.types import Ship
from pvpdata import sitefiles

RESOURCE_REPO_URL = r'https://github.com/Fernando2603/AzurLane.git'
PVP_SHIP_FILE = sitefiles.get_data_path(Ship)


def exec_gamefiles_git(ctx, command):
    with ctx.cd(GAME_RESOURCES_DIR):
        return ctx.run('git ' + command, echo=True)


@task
def cleangamefiles(ctx):
    if GAME_RESOURCES_DIR.is_dir():
        with ctx.cd(PROJECT_ROOT):
            # Must use special function because Git applies some attributes
            # that prevent normal deletion from working.
            print(f'Deleting {GAME_RESOURCES_DIR.name}', end=' ')
            if not ctx.config.run.dry:
                print('...', end=' ')
                git_rmtree(GAME_RESOURCES_DIR)
                print('Complete')
            else:
                print('(dry)')
    else:
        print(f'{GAME_RESOURCES_DIR.name} did not exist or is not a directory')


@task
def initgamefiles(ctx):
    print(f'Ensuring {GAME_RESOURCES_DIR.name} clone is initialized')
    if not GAME_RESOURCES_DIR.exists():
        print(f'Creating {GAME_RESOURCES_DIR.name}')
        if not ctx.config.run.dry:
            GAME_RESOURCES_DIR.mkdir()
    elif not GAME_RESOURCES_DIR.is_dir():
        raise Exit(f'Cannot create repository at {GAME_RESOURCES_DIR}: not a directory.')

    if not is_git_dir(GAME_RESOURCES_DIR / '.git'):
        if any(GAME_RESOURCES_DIR.iterdir()):
            raise Exit(f'Cannot create repository at {GAME_RESOURCES_DIR}: not an empty directory.')

        ctxgit = partial(exec_gamefiles_git, ctx)

        # Initialize repo with options to minimize disk space and downloads
        # --no-checkout prevents immediate full checkout, which would download everything.
        # --depth=1 prevents fetching full history. We only need recent commits.
        # --filter=blob:none delays downloading of file content until they are checked out to disk.
        ctxgit(f'clone --no-checkout --depth=1 --filter=blob:none {RESOURCE_REPO_URL} .')
        # Initially checkout only files at root, which includes JSON data files
        ctxgit('sparse-checkout init')
        ctxgit('checkout')
        print(f'Set up {GAME_RESOURCES_DIR.name} with sparse checkout and shallow clone')
    else:
        print(f'{GAME_RESOURCES_DIR.name} clone already set up')


def try_extract_skin_id(s):
    if not isinstance(s, dict):
        return None

    skin_id = s.get('skin_id')

    if not isinstance(skin_id, int):
        return None

    return skin_id


@task(initgamefiles)
def updategamefiles(ctx):
    print(f'Updating {GAME_RESOURCES_DIR.name} clone')
    # Equipment directory is relatively small, so we can just include to whole thing.
    filedirs = ['images/equipment/']

    # Just keep going if there's any problem loading skin data from ships
    if PVP_SHIP_FILE.is_file():
        with open(PVP_SHIP_FILE, encoding='utf-8') as f:
            try:
                ship_data = json.load(f)
                if not isinstance(ship_data, dict):
                    raise NotImplementedError(f'ship_data is a {type(ship_data)}; expected dict')
            except:
                print(f'Malformed {PVP_SHIP_FILE.name}. Skipping skin IDs.')
            else:
                # Only runs on successful try block
                for name, ship in ship_data.items():
                    if skin_id := try_extract_skin_id(ship):
                        filedirs.append(f'images/skins/{skin_id}/')
                    else:
                        print('Malformed ship or missing skin_id: {name}. Skipping skin_id.')
    else:
        print(f'{PVP_SHIP_FILE.name} not found')

    ctxgit = partial(exec_gamefiles_git, ctx)

    ctxgit('sparse-checkout set ' + ' '.join(filedirs))
    ctxgit('fetch --filter=blob:none')
    ctxgit('reset --hard origin/main')


@task(cleangamefiles, updategamefiles)
def recreategamefiles(ctx):
    pass
