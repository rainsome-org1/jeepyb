#!/usr/bin/env python
# Copyright (c) 2011 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# This is designed to be called by a gerrit hook.  It searched new
# patchsets for strings like "bug FOO" and updates corresponding Launchpad
# bugs status.

import argparse
import os
import re
import subprocess

from launchpadlib import launchpad
from launchpadlib import uris

import jeepyb.gerritdb

BASE_DIR = '/home/gerrit2/review_site'
GERRIT_CACHE_DIR = os.path.expanduser(
    os.environ.get('GERRIT_CACHE_DIR',
                   '~/.launchpadlib/cache'))
GERRIT_CREDENTIALS = os.path.expanduser(
    os.environ.get('GERRIT_CREDENTIALS',
                   '~/.launchpadlib/creds'))


def add_change_proposed_message(bugtask, change_url, project, branch):
    subject = 'Fix proposed to %s (%s)' % (short_project(project), branch)
    body = 'Fix proposed to branch: %s\nReview: %s' % (branch, change_url)
    bugtask.bug.newMessage(subject=subject, content=body)


def add_change_merged_message(bugtask, change_url, project, commit,
                              submitter, branch, git_log):
    subject = 'Fix merged to %s (%s)' % (short_project(project), branch)
    git_url = 'http://github.com/%s/commit/%s' % (project, commit)
    body = '''Reviewed:  %s
Committed: %s
Submitter: %s
Branch:    %s\n''' % (change_url, git_url, submitter, branch)
    body = body + '\n' + git_log
    bugtask.bug.newMessage(subject=subject, content=body)


def set_in_progress(bugtask, launchpad, uploader, change_url):
    """Set bug In progress with assignee being the uploader"""

    # Retrieve uploader from Launchpad by correlating Gerrit E-mail
    # address to OpenID, and only set if there is a clear match.
    try:
        searchkey = uploader[uploader.rindex("(") + 1:-1]
    except ValueError:
        searchkey = uploader

    # The counterintuitive query is due to odd database schema choices
    # in Gerrit. For example, an account with a secondary E-mail
    # address added looks like...
    # select email_address,external_id from account_external_ids
    #     where account_id=1234;
    # +-----------------+-----------------------------------------+
    # | email_address   | external_id                             |
    # +-----------------+-----------------------------------------+
    # | plugh@xyzzy.com | https://login.launchpad.net/+id/fR0bnU1 |
    # | bar@foo.org     | mailto:bar@foo.org                      |
    # | NULL            | username:quux                           |
    # +-----------------+-----------------------------------------+
    # ...thus we need a join on a secondary query to search against
    # all the user's configured E-mail addresses.
    #
    query = """SELECT t.external_id FROM account_external_ids t
            INNER JOIN (
                SELECT t.account_id FROM account_external_ids t
                WHERE t.email_address = %s )
            original ON t.account_id = original.account_id
            AND t.external_id LIKE 'https://login.launchpad.net%%'"""

    cursor = jeepyb.gerritdb.connect().cursor()
    cursor.execute(query, searchkey)
    data = cursor.fetchone()
    if data:
        assignee = launchpad.people.getByOpenIDIdentifier(identifier=data[0])
        if assignee:
            bugtask.assignee = assignee

    bugtask.status = "In Progress"
    bugtask.lp_save()


def set_fix_committed(bugtask):
    """Set bug fix committed."""

    bugtask.status = "Fix Committed"
    bugtask.lp_save()


def set_fix_released(bugtask):
    """Set bug fix released."""

    bugtask.status = "Fix Released"
    bugtask.lp_save()


def release_fixcommitted(bugtask):
    """Set bug FixReleased if it was FixCommitted."""

    if bugtask.status == u'Fix Committed':
        set_fix_released(bugtask)


def tag_in_branchname(bugtask, branch):
    """Tag bug with in-branch-name tag (if name is appropriate)."""

    lp_bug = bugtask.bug
    branch_name = branch.replace('/', '-')
    if branch_name.replace('-', '').isalnum():
        lp_bug.tags = lp_bug.tags + ["in-%s" % branch_name]
        lp_bug.tags.append("in-%s" % branch_name)
        lp_bug.lp_save()


def short_project(full_project_name):
    """Return the project part of the git repository name."""
    return full_project_name.split('/')[-1]


def git2lp(full_project_name):
    """Convert Git repo name to Launchpad project."""
    project_map = {
        'openstack/api-site': 'openstack-api-site',
        'openstack/quantum': 'neutron',
        'openstack/python-quantumclient': 'python-neutronclient',
        'openstack/oslo-incubator': 'oslo',
        'openstack/tripleo-incubator': 'tripleo',
        'openstack-infra/askbot-theme': 'openstack-ci',
        'openstack-infra/config': 'openstack-ci',
        'openstack-infra/devstack-gate': 'openstack-ci',
        'openstack-infra/gear': 'openstack-ci',
        'openstack-infra/gerrit': 'openstack-ci',
        'openstack-infra/gerritbot': 'openstack-ci',
        'openstack-infra/gerritlib': 'openstack-ci',
        'openstack-infra/gitdm': 'openstack-ci',
        'openstack-infra/jeepyb': 'openstack-ci',
        'openstack-infra/jenkins-job-builder': 'openstack-ci',
        'openstack-infra/lodgeit': 'openstack-ci',
        'openstack-infra/meetbot': 'openstack-ci',
        'openstack-infra/nose-html-output': 'openstack-ci',
        'openstack-infra/publications': 'openstack-ci',
        'openstack-infra/puppet-apparmor': 'openstack-ci',
        'openstack-infra/puppet-dashboard': 'openstack-ci',
        'openstack-infra/puppet-vcsrepo': 'openstack-ci',
        'openstack-infra/reviewday': 'openstack-ci',
        'openstack-infra/statusbot': 'openstack-ci',
        'openstack-infra/zmq-event-publisher': 'openstack-ci',
        'stackforge/cookbook-openstack-block-storage': 'openstack-chef',
        'stackforge/cookbook-openstack-common': 'openstack-chef',
        'stackforge/cookbook-openstack-compute': 'openstack-chef',
        'stackforge/cookbook-openstack-dashboard': 'openstack-chef',
        'stackforge/cookbook-openstack-identity': 'openstack-chef',
        'stackforge/cookbook-openstack-image': 'openstack-chef',
        'stackforge/cookbook-openstack-metering': 'openstack-chef',
        'stackforge/cookbook-openstack-network': 'openstack-chef',
        'stackforge/cookbook-openstack-object-storage': 'openstack-chef',
        'stackforge/cookbook-openstack-ops-database': 'openstack-chef',
        'stackforge/cookbook-openstack-ops-messaging': 'openstack-chef',
        'stackforge/cookbook-openstack-orchestration': 'openstack-chef',
        'stackforge/openstack-chef-repo': 'openstack-chef',
        'stackforge/puppet-openstack_dev_env': 'puppet-openstack',
        'stackforge/puppet-quantum': 'puppet-neutron',
        'stackforge/tripleo-heat-templates': 'tripleo',
        'stackforge/tripleo-image-elements': 'tripleo',
    }
    return project_map.get(full_project_name, short_project(full_project_name))


def is_direct_release(full_project_name):
    """Test against a list of projects who directly release changes."""
    return full_project_name in [
        'openstack/openstack-manuals',
        'openstack/api-site',
        'openstack/tripleo-incubator',
        'openstack-dev/devstack',
        'openstack-infra/askbot-theme',
        'openstack-infra/config',
        'openstack-infra/devstack-gate',
        'openstack-infra/gerrit',
        'openstack-infra/gerritbot',
        'openstack-infra/gerritlib',
        'openstack-infra/gitdm',
        'openstack-infra/lodgeit',
        'openstack-infra/meetbot',
        'openstack-infra/nose-html-output',
        'openstack-infra/publications',
        'openstack-infra/reviewday',
        'openstack-infra/statusbot',
        'stackforge/cookbook-openstack-block-storage',
        'stackforge/cookbook-openstack-common',
        'stackforge/cookbook-openstack-compute',
        'stackforge/cookbook-openstack-dashboard',
        'stackforge/cookbook-openstack-identity',
        'stackforge/cookbook-openstack-image',
        'stackforge/cookbook-openstack-metering',
        'stackforge/cookbook-openstack-network',
        'stackforge/cookbook-openstack-object-storage',
        'stackforge/cookbook-openstack-ops-database',
        'stackforge/cookbook-openstack-ops-messaging',
        'stackforge/cookbook-openstack-orchestration',
        'stackforge/openstack-chef-repo',
        'stackforge/tripleo-heat-templates',
        'stackforge/tripleo-image-elements',
    ]


class Task:
    def __init__(self, lp_task, prefix):
        '''Prefixes associated with bug references will allow for certain
        changes to be made to the bug's launchpad (lp) page. The following
        tokens represent the automation currently taking place.

        ::
        add_comment       -> Adds a comment to the bug's lp page.
        set_in_progress   -> Sets the bug's lp status to 'In Progress'.
        set_fix_released  -> Sets the bug's lp status to 'Fix Released'.
        set_fix_committed -> Sets the bug's lp status to 'Fix Committed'.
        ::

        changes_needed, when populated, simply indicates the actions that are
        available to be taken based on the value of 'prefix'.
        '''
        self.lp_task = lp_task
        self.changes_needed = []

        # If no prefix was matched, default to 'closes'.
        prefix = prefix.split('-')[0].lower() if prefix else 'closes'

        if prefix in ('closes', 'fixes', 'resolves'):
            self.changes_needed.extend(('add_comment',
                                        'set_in_progress',
                                        'set_fix_committed',
                                        'set_fix_released'))
        elif prefix in ('partial',):
            self.changes_needed.extend(('add_comment', 'set_in_progress'))
        elif prefix in ('related', 'impacts', 'affects'):
            self.changes_needed.extend(('add_comment',))
        else:
            # prefix is not recognized.
            self.changes_needed.extend(('add_comment',))

    def needs_change(self, change):
        '''Return a boolean indicating if given 'change' needs to be made.'''
        if change in self.changes_needed:
            return True
        else:
            return False


def process_bugtask(launchpad, task, git_log, args):
    """Apply changes to lp bug tasks, based on hook / branch."""

    bugtask = task.lp_task

    if args.hook == "change-merged":
        if args.branch == 'master':
            if (is_direct_release(args.project) and
                    task.needs_change('set_fix_released')):
                set_fix_released(bugtask)
            else:
                if (bugtask.status != u'Fix Released' and
                        task.needs_change('set_fix_committed')):
                    set_fix_committed(bugtask)
        elif args.branch == 'milestone-proposed':
            release_fixcommitted(bugtask)
        elif args.branch.startswith('stable/'):
            series = args.branch[7:]
            # Look for a related task matching the series.
            for reltask in bugtask.related_tasks:
                if (reltask.bug_target_name.endswith("/" + series) and
                        reltask.status != u'Fix Released' and
                        task.needs_change('set_fix_committed')):
                    set_fix_committed(reltask)
                    break
            else:
                # Use tag_in_branchname if there isn't any.
                tag_in_branchname(bugtask, args.branch)

        add_change_merged_message(bugtask, args.change_url, args.project,
                                  args.commit, args.submitter, args.branch,
                                  git_log)

    if args.hook == "patchset-created":
        if args.branch == 'master':
            if (bugtask.status not in [u'Fix Committed', u'Fix Released'] and
                    task.needs_change('set_in_progress')):
                set_in_progress(bugtask, launchpad,
                                args.uploader, args.change_url)
        elif args.branch.startswith('stable/'):
            series = args.branch[7:]
            for reltask in bugtask.related_tasks:
                if (reltask.bug_target_name.endswith("/" + series) and
                        task.needs_change('set_in_progress') and
                        reltask.status not in [u'Fix Committed',
                                               u'Fix Released']):
                    set_in_progress(reltask, launchpad,
                                    args.uploader, args.change_url)
                    break

        if args.patchset == '1':
            add_change_proposed_message(bugtask, args.change_url,
                                        args.project, args.branch)


def find_bugs(launchpad, git_log, args):
    '''Find bugs referenced in the git log and return related tasks.

    Our regular expression is composed of three major parts:
    part1: Matches only at start-of-line (required). Optionally matches any
           word or hyphen separated words.
    part2: Matches the words 'bug' or 'lp' on a word boundry (required).
    part3: Matches a whole number (required).

    The following patterns will be matched properly:
    bug # 555555
    Closes-Bug: 555555
    Fixes: bug # 555555
    Resolves: bug 555555
    Partial-Bug: lp bug # 555555

    :returns: an iterable containing Task objects.
    '''

    part1 = r'^[\t ]*(?P<prefix>[-\w]+)?[\s:]*'
    part2 = r'(?:\b(?:bug|lp)\b[\s#:]*)+'
    part3 = r'(?P<bug_number>\d+)\s*?$'
    regexp = part1 + part2 + part3
    matches = re.finditer(regexp, git_log, flags=re.I | re.M)

    # Extract unique bug tasks and associated prefixes.
    bugtasks = {}
    for match in matches:
        prefix = match.group('prefix')
        bug_num = match.group('bug_number')
        if bug_num not in bugtasks:
            try:
                lp_bug = launchpad.bugs[bug_num]
                for lp_task in lp_bug.bug_tasks:
                    if lp_task.bug_target_name == git2lp(args.project):
                        bugtasks[bug_num] = Task(lp_task, prefix)
                        break
            except KeyError:
                # Unknown bug.
                pass

    return bugtasks.values()


def extract_git_log(args):
    """Extract git log of all merged commits."""
    cmd = ['git',
           '--git-dir=' + BASE_DIR + '/git/' + args.project + '.git',
           'log', '--no-merges', args.commit + '^1..' + args.commit]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE).communicate()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('hook')
    # common
    parser.add_argument('--change', default=None)
    parser.add_argument('--change-url', default=None)
    parser.add_argument('--project', default=None)
    parser.add_argument('--branch', default=None)
    parser.add_argument('--commit', default=None)
    # change-merged
    parser.add_argument('--submitter', default=None)
    # patchset-created
    parser.add_argument('--uploader', default=None)
    parser.add_argument('--patchset', default=None)

    args = parser.parse_args()

    # Connect to Launchpad.
    lpconn = launchpad.Launchpad.login_with(
        'Gerrit User Sync', uris.LPNET_SERVICE_ROOT, GERRIT_CACHE_DIR,
        credentials_file=GERRIT_CREDENTIALS, version='devel')

    # Get git log.
    git_log = extract_git_log(args)

    # Process tasks found in git log.
    for task in find_bugs(lpconn, git_log, args):
        process_bugtask(lpconn, task, git_log, args)

if __name__ == "__main__":
    main()
