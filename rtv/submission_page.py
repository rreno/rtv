# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import time
import curses

from . import docs
from .content import SubmissionContent, SubredditContent
from .page import Page, PageController, logged_in
from .objects import Navigator, Color, Command
from .exceptions import TemporaryFileError


class SubmissionController(PageController):
    character_map = {}


class SubmissionPage(Page):

    FOOTER = docs.FOOTER_SUBMISSION

    def __init__(self, reddit, term, config, oauth, url=None, submission=None):
        super(SubmissionPage, self).__init__(reddit, term, config, oauth)

        self.controller = SubmissionController(self, keymap=config.keymap)

        if url:
            self.content = SubmissionContent.from_url(
                reddit, url, term.loader,
                max_comment_cols=config['max_comment_cols'])
        else:
            self.content = SubmissionContent(
                submission, term.loader,
                max_comment_cols=config['max_comment_cols'])
        # Start at the submission post, which is indexed as -1
        self.nav = Navigator(self.content.get, page_index=-1)
        self.selected_subreddit = None

    @SubmissionController.register(Command('SUBMISSION_TOGGLE_COMMENT'))
    def toggle_comment(self):
        """
        Toggle the selected comment tree between visible and hidden
        """

        current_index = self.nav.absolute_index
        self.content.toggle(current_index)

        # This logic handles a display edge case after a comment toggle. We
        # want to make sure that when we re-draw the page, the cursor stays at
        # its current absolute position on the screen. In order to do this,
        # apply a fixed offset if, while inverted, we either try to hide the
        # bottom comment or toggle any of the middle comments.
        if self.nav.inverted:
            data = self.content.get(current_index)
            if data['hidden'] or self.nav.cursor_index != 0:
                window = self._subwindows[-1][0]
                n_rows, _ = window.getmaxyx()
                self.nav.flip(len(self._subwindows) - 1)
                self.nav.top_item_height = n_rows

    @SubmissionController.register(Command('SUBMISSION_EXIT'))
    def exit_submission(self):
        """
        Close the submission and return to the subreddit page
        """

        self.active = False

    @SubmissionController.register(Command('REFRESH'))
    def refresh_content(self, order=None, name=None):
        """
        Re-download comments and reset the page index
        """

        order = order or self.content.order
        url = name or self.content.name

        # Hack to allow an order specified in the name by prompt_subreddit() to
        # override the current default
        if order == 'ignore':
            order = None

        with self.term.loader('Refreshing page'):
            self.content = SubmissionContent.from_url(
                self.reddit, url, self.term.loader, order=order,
                max_comment_cols=self.config['max_comment_cols'])
        if not self.term.loader.exception:
            self.nav = Navigator(self.content.get, page_index=-1)

    @SubmissionController.register(Command('PROMPT'))
    def prompt_subreddit(self):
        """
        Open a prompt to navigate to a different subreddit
        """

        name = self.term.prompt_input('Enter page: /')
        if name is not None:
            # Check if opening a submission url or a subreddit url
            # Example patterns for submissions:
            #     comments/571dw3
            #     /comments/571dw3
            #     /r/pics/comments/571dw3/
            #     https://www.reddit.com/r/pics/comments/571dw3/at_disneyland
            submission_pattern = re.compile(r'(^|/)comments/(?P<id>.+?)($|/)')
            match = submission_pattern.search(name)
            if match:
                url = 'https://www.reddit.com/comments/{0}'
                self.refresh_content('ignore', url.format(match.group('id')))

            else:
                with self.term.loader('Loading page'):
                    content = SubredditContent.from_name(
                        self.reddit, name, self.term.loader)
                if not self.term.loader.exception:
                    self.selected_subreddit = content
                    self.active = False

    @SubmissionController.register(Command('SUBMISSION_OPEN_IN_BROWSER'))
    def open_link(self):
        """
        Open the selected item with the webbrowser
        """

        data = self.get_selected_item()
        if data['type'] == 'Submission':
            self.term.open_link(data['url_full'])
            self.config.history.add(data['url_full'])
        elif data['type'] == 'Comment' and data['permalink']:
            self.term.open_browser(data['permalink'])
        else:
            self.term.flash()

    @SubmissionController.register(Command('SUBMISSION_OPEN_IN_PAGER'))
    def open_pager(self):
        """
        Open the selected item with the system's pager
        """

        data = self.get_selected_item()
        if data['type'] == 'Submission':
            text = '\n\n'.join((data['permalink'], data['text']))
            self.term.open_pager(text)
        elif data['type'] == 'Comment':
            text = '\n\n'.join((data['permalink'], data['body']))
            self.term.open_pager(text)
        else:
            self.term.flash()

    @SubmissionController.register(Command('SUBMISSION_POST'))
    @logged_in
    def add_comment(self):
        """
        Submit a reply to the selected item.

        Selected item:
            Submission - add a top level comment
            Comment - add a comment reply
        """

        data = self.get_selected_item()
        if data['type'] == 'Submission':
            body = data['text']
            reply = data['object'].add_comment
        elif data['type'] == 'Comment':
            body = data['body']
            reply = data['object'].reply
        else:
            self.term.flash()
            return

        # Construct the text that will be displayed in the editor file.
        # The post body will be commented out and added for reference
        lines = ['#  |' + line for line in body.split('\n')]
        content = '\n'.join(lines)
        comment_info = docs.COMMENT_FILE.format(
            author=data['author'],
            type=data['type'].lower(),
            content=content)

        with self.term.open_editor(comment_info) as comment:
            if not comment:
                self.term.show_notification('Canceled')
                return

            with self.term.loader('Posting', delay=0):
                reply(comment)
                # Give reddit time to process the submission
                time.sleep(2.0)

            if self.term.loader.exception is None:
                self.refresh_content()
            else:
                raise TemporaryFileError()

    @SubmissionController.register(Command('DELETE'))
    @logged_in
    def delete_comment(self):
        """
        Delete the selected comment
        """

        if self.get_selected_item()['type'] == 'Comment':
            self.delete_item()
        else:
            self.term.flash()

    @SubmissionController.register(Command('SUBMISSION_OPEN_IN_URLVIEWER'))
    def comment_urlview(self):
        data = self.get_selected_item()
        comment = data.get('body') or data.get('text') or data.get('url_full')
        if comment:
            self.term.open_urlview(comment)
        else:
            self.term.flash()

    @SubmissionController.register(Command('SUBMISSION_GOTO_PARENT'))
    def move_parent_up(self):
        """
        Move the cursor up to the comment's parent. If the comment is
        top-level, jump to the previous top-level comment.
        """

        cursor = self.nav.absolute_index
        if cursor > 0:
            level = max(self.content.get(cursor)['level'], 1)
            while self.content.get(cursor - 1)['level'] >= level:
                self._move_cursor(-1)
                cursor -= 1
            self._move_cursor(-1)
        else:
            self.term.flash()

        self.clear_input_queue()

    @SubmissionController.register(Command('SUBMISSION_GOTO_SIBLING'))
    def move_sibling_next(self):
        """
        Jump to the next comment that's at the same level as the selected
        comment and shares the same parent.
        """

        cursor = self.nav.absolute_index
        if cursor >= 0:
            level = self.content.get(cursor)['level']
            try:
                move = 1
                while self.content.get(cursor + move)['level'] > level:
                    move += 1
            except IndexError:
                self.term.flash()
            else:
                if self.content.get(cursor + move)['level'] == level:
                    for _ in range(move):
                        self._move_cursor(1)
                else:
                    self.term.flash()
        else:
            self.term.flash()

        self.clear_input_queue()

    def _draw_item(self, win, data, inverted):

        if data['type'] == 'MoreComments':
            return self._draw_more_comments(win, data)
        elif data['type'] == 'HiddenComment':
            return self._draw_more_comments(win, data)
        elif data['type'] == 'Comment':
            return self._draw_comment(win, data, inverted)
        else:
            return self._draw_submission(win, data)

    def _draw_comment(self, win, data, inverted):

        n_rows, n_cols = win.getmaxyx()
        n_cols -= 1

        # Handle the case where the window is not large enough to fit the text.
        valid_rows = range(0, n_rows)
        offset = 0 if not inverted else -(data['n_rows'] - n_rows)

        # If there isn't enough space to fit the comment body on the screen,
        # replace the last line with a notification.
        split_body = data['split_body']
        if data['n_rows'] > n_rows:
            # Only when there is a single comment on the page and not inverted
            if not inverted and len(self._subwindows) == 0:
                cutoff = data['n_rows'] - n_rows + 1
                split_body = split_body[:-cutoff]
                split_body.append('(Not enough space to display)')

        row = offset
        if row in valid_rows:

            attr = curses.A_BOLD
            attr |= (Color.BLUE if not data['is_author'] else Color.GREEN)
            text = '{author} '.format(**data)
            if data['is_author']:
                text += '[S] '
            self.term.add_line(win, text, row, 1, attr)

            if data['flair']:
                attr = curses.A_BOLD | Color.YELLOW
                self.term.add_line(win, '{flair} '.format(**data), attr=attr)

            text, attr = self.term.get_arrow(data['likes'])
            self.term.add_line(win, text, attr=attr)
            self.term.add_line(win, ' {score} {created} '.format(**data))

            if data['gold']:
                text, attr = self.term.guilded
                self.term.add_line(win, text, attr=attr)

            if data['stickied']:
                text, attr = '[stickied]', Color.GREEN
                self.term.add_line(win, text, attr=attr)

            if data['saved']:
                text, attr = '[saved]', Color.GREEN
                self.term.add_line(win, text, attr=attr)

        for row, text in enumerate(split_body, start=offset+1):
            if row in valid_rows:
                self.term.add_line(win, text, row, 1)

        # Unfortunately vline() doesn't support custom color so we have to
        # build it one segment at a time.
        attr = Color.get_level(data['level'])
        x = 0
        for y in range(n_rows):
            self.term.addch(win, y, x, self.term.vline, attr)

        return attr | self.term.vline

    def _draw_more_comments(self, win, data):

        n_rows, n_cols = win.getmaxyx()
        n_cols -= 1

        self.term.add_line(win, '{body}'.format(**data), 0, 1)
        self.term.add_line(
            win, ' [{count}]'.format(**data), attr=curses.A_BOLD)

        attr = Color.get_level(data['level'])
        self.term.addch(win, 0, 0, self.term.vline, attr)

        return attr | self.term.vline

    def _draw_submission(self, win, data):

        n_rows, n_cols = win.getmaxyx()
        n_cols -= 3  # one for each side of the border + one for offset

        for row, text in enumerate(data['split_title'], start=1):
            self.term.add_line(win, text, row, 1, curses.A_BOLD)

        row = len(data['split_title']) + 1
        attr = curses.A_BOLD | Color.GREEN
        self.term.add_line(win, '{author}'.format(**data), row, 1, attr)
        attr = curses.A_BOLD | Color.YELLOW
        if data['flair']:
            self.term.add_line(win, ' {flair}'.format(**data), attr=attr)
        self.term.add_line(win, ' {created} {subreddit}'.format(**data))

        row = len(data['split_title']) + 2
        seen = (data['url_full'] in self.config.history)
        link_color = Color.MAGENTA if seen else Color.BLUE
        attr = curses.A_UNDERLINE | link_color
        self.term.add_line(win, '{url}'.format(**data), row, 1, attr)
        offset = len(data['split_title']) + 3

        # Cut off text if there is not enough room to display the whole post
        split_text = data['split_text']
        if data['n_rows'] > n_rows:
            cutoff = data['n_rows'] - n_rows + 1
            split_text = split_text[:-cutoff]
            split_text.append('(Not enough space to display)')

        for row, text in enumerate(split_text, start=offset):
            self.term.add_line(win, text, row, 1)

        row = len(data['split_title']) + len(split_text) + 3
        self.term.add_line(win, '{score} '.format(**data), row, 1)
        text, attr = self.term.get_arrow(data['likes'])
        self.term.add_line(win, text, attr=attr)
        self.term.add_line(win, ' {comments} '.format(**data))

        if data['gold']:
            text, attr = self.term.guilded
            self.term.add_line(win, text, attr=attr)

        if data['nsfw']:
            text, attr = 'NSFW', (curses.A_BOLD | Color.RED)
            self.term.add_line(win, text, attr=attr)

        if data['saved']:
            text, attr = '[saved]', Color.GREEN
            self.term.add_line(win, text, attr=attr)

        win.border()
