"""Removing duplicate introductions must not restyle the retained occurrence."""
from __future__ import annotations

import copy
import logging

import pytest

import bookir as ir
import glossary as gl
import naming
import qanaming
from findings import Report

LOG = logging.getLogger(__name__)


def terms_for(policy):
    terms = gl.new_glossary()
    terms['policy']['original_parenthetical'] = policy
    entry = gl.make_entry(1, 'Ali', category='person')
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True,
                 first_block_id='b00001')
    terms['entries'] = [entry]
    return terms


def check_gate(book, terms):
    report = Report()
    qanaming._check_first_mentions(book, terms, report)
    assert report.summary()['ok'], report.summary()


@pytest.mark.parametrize('policy', ['first_mention', 'first_per_chapter'])
@pytest.mark.parametrize('first', ['**علی** (Ali)', 'علی *(Ali)*', '***علی*** **(Ali)**', '**علی (Ali)**'])
@pytest.mark.parametrize('later', ['**علی** *(Ali)*', 'علی (Ali)'])
def test_keep_first_occurrence_and_remove_only_later_parentheticals(policy, first, later):
    book = ir.new_book(source_format='epub')
    target = first + ' آمد. سپس ' + later + ' رفت.'
    book['blocks'] = [ir.make_block('paragraph', 1, text='Ali came. Ali left.', target=target)]
    terms = terms_for(policy)
    expected = first + ' آمد. سپس ' + ('**علی**' if later.startswith('**') else 'علی') + ' رفت.'
    for _ in range(3):
        naming.enforce_first_mentions(terms, book)
        assert book['blocks'][0]['target'] == expected
        check_gate(book, terms)
    LOG.debug('Retained original first-introduction styling under %s', policy)


@pytest.mark.parametrize('policy', ['never', 'first_mention', 'first_per_chapter'])
def test_three_occurrences_close_in_one_pass_and_stay_a_fixed_point(policy):
    book = ir.new_book(source_format='epub')
    target = '**علی** (Ali) آمد. علی *(Ali)* دید. *علی* (Ali) رفت.'
    book['blocks'] = [ir.make_block('paragraph', 1, text='Ali came, saw and left.', target=target)]
    terms = terms_for(policy)
    result = naming.enforce_first_mentions(terms, book)
    assert result['flattened'] == (3 if policy == 'never' else 2)
    check_gate(book, terms)
    saved = copy.deepcopy(book)
    assert naming.enforce_first_mentions(terms, book)['flattened'] == 0
    assert book == saved


def test_each_chapter_preserves_its_own_retained_style():
    book = ir.new_book(source_format='epub')
    book['blocks'] = [
        ir.make_block('heading', 1, level=1, text='Chapter One', target='فصل اول'),
        ir.make_block('paragraph', 2, text='Ali', target='**علی** (Ali) آمد. علی (Ali) رفت.'),
        ir.make_block('heading', 3, level=1, text='Chapter Two', target='فصل دوم'),
        ir.make_block('paragraph', 4, text='Ali', target='علی *(Ali)* آمد. علی (Ali) رفت.')]
    terms = terms_for('first_per_chapter')
    naming.enforce_first_mentions(terms, book)
    assert book['blocks'][1]['target'] == '**علی** (Ali) آمد. علی رفت.'
    assert book['blocks'][3]['target'] == 'علی *(Ali)* آمد. علی رفت.'
    check_gate(book, terms)
