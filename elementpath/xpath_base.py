# -*- coding: utf-8 -*-
#
# Copyright (c), 2018, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
import sys
from .exceptions import ElementPathTypeError, ElementPathValueError
from .todp_parser import Token


XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
XML_ID_ATTRIBUTE = '{%s}id' % XML_NAMESPACE


###
# XPath node types test functions
#
# In XPath there are 7 kinds of nodes:
#
#    element, attribute, text, namespace, processing-instruction, comment, document
#
# Element-like objects are used for representing elements and comments, ElementTree-like objects
# for documents. Generic tuples are used for representing attributes and named-tuples
# for namespaces.
#
def is_etree_element(obj):
    return hasattr(obj, 'tag') and hasattr(obj, 'attrib') and hasattr(obj, 'text')


def is_element_node(obj, tag=None):
    if tag is None:
        return is_etree_element(obj) and not callable(obj.tag)
    else:
        return is_etree_element(obj) and obj.tag == tag


def is_comment_node(obj):
    return is_etree_element(obj) and callable(obj.tag) and obj.tag.__name__ == 'Comment'


def is_processing_instruction_node(obj):
    return is_etree_element(obj) and callable(obj.tag) and obj.tag.__name__ == 'ProcessingInstruction'


def is_document_node(obj):
    return all(hasattr(obj, name) for name in ('getroot', 'iter', 'iterfind', 'parse'))


def is_attribute_node(obj, name=None):
    if name is None:
        return isinstance(obj, tuple) and getattr(obj, '__name__', '') != 'Namespace'
    elif isinstance(obj, tuple) and getattr(obj, '__name__', '') != 'Namespace':
        return name == obj[0]


def is_namespace_node(obj):
    return isinstance(obj, tuple) and getattr(obj, '__name__', '') == 'Namespace'


if sys.version_info[0] < 3:
    def is_text_node(obj):
        return isinstance(obj, (str, unicode))
else:
    def is_text_node(obj):
        return isinstance(obj, str)


def is_xpath_node(obj):
    return isinstance(obj, tuple) or is_etree_element(obj) or is_document_node(obj) or is_text_node(obj)


###
# XPathToken
class XPathToken(Token):

    def select(self, context):
        """
        Select operator that generates results

        :param context: The XPath evaluation context.
        """
        item = self.evaluate(context)
        if item is not None:
            context.item = item
            yield item

    def __str__(self):
        symbol, label = self.symbol, self.label
        if symbol == '$':
            return '$%s variable reference' % str(self[0].evaluate() if self else '')
        elif symbol == ',':
            return 'comma operator'
        elif label == 'function':
            return '%s(%s) function' % (symbol, ', '.join(repr(t.value) for t in self))
        elif label == 'axis':
            return '%s axis' % symbol
        return super(XPathToken, self).__str__()

    # Helper methods
    def boolean(self, value):
        """The effective boolean value, computed by fn:boolean()."""
        if isinstance(value, list):
            if not value:
                return False
            elif is_xpath_node(value[0]):
                return True
            elif len(value) > 1:
                self.wrong_type("not a test expression")
            else:
                return bool(value[0])
        elif isinstance(value, tuple) or is_etree_element(value):
            self.wrong_type("not a test expression")
        else:
            return bool(value)

    def name(self, value):
        if is_element_node(value):
            return value.tag
        elif is_attribute_node(value):
            return value[0]
        elif is_document_node(value) or is_namespace_node(value):
            return ''
        elif value or isinstance(value, list) and not value:
            return ''
        else:
            self.wrong_type("an XPath node required: %r" % value)

    # Errors
    def missing_context(self):
        raise ElementPathValueError("%s: dynamic context required for evaluate." % self)


class XPathContext(object):
    """
    XPath expressions dynamic context. The static context is provided by the parser.

    :ivar root: The root of the XML document, must be a ElementTree's Element.
    :ivar item: The context item. A `None` value means that the context is positioned on \
    the document node.
    :ivar position: The current position of the node within the input sequence.
    :ivar size: The number of items in the input sequence.
    :ivar variables: Dictionary of context variables that maps a QName to a value.
    """
    def __init__(self, root, item=None, position=0, size=1, variables=None):
        if not is_element_node(root) and not is_document_node(root):
            raise ElementPathTypeError("argument 'root' must be an Element: %r" % root)
        self.root = root
        if item is not None:
            self.item = item
        elif is_element_node(root):
            self.item = root
        else:
            self.item = root.getroot()

        self.position = position
        self.size = size
        self.variables = {} if variables is None else variables
        self._parent_map = None
        self._iterator = None
        self._node_kind_test = is_element_node

    def __repr__(self):
        return '%s(root=%r, item=%r, position=%r, size=%r)' % (
            self.__class__.__name__, self.root, self.item, self.position, self.size
        )

    def copy(self, item=None):
        obj = XPathContext(
            root=self.root,
            item=self.item if item is None else item,
            position=self.position,
            size=self.size,
            variables=self.variables.copy()
        )
        obj._parent_map = self._parent_map
        return obj

    @property
    def parent_map(self):
        if self._parent_map is None:
            self._parent_map = {child: elem for elem in self.root.iter() for child in elem}
        return self._parent_map

    @property
    def active_iterator(self):
        return self._iterator

    @property
    def principal_node_kind(self):
        return self._node_kind_test(self.item)

    # Context item iterators
    def iter_self(self):
        status = self.item, self.size, self.position, self._iterator
        self._iterator, self._node_kind_test = self.iter_self, is_element_node

        yield self.item
        self.item, self.size, self.position, self._iterator = status

    def iter_attributes(self):
        if is_element_node(self.item):
            status = self.item, self.size, self.position, self._iterator
            self._iterator, self._node_kind_test = self.iter_self, is_attribute_node

            for item in sorted(self.item.attrib.items()):
                self.item = item
                yield item

            self.item, self.size, self.position, self._iterator = status
            self._node_kind_test = is_element_node

    def iter_descendants(self, item=None):
        def _iter_descendants():
            elem = self.item
            yield self.item
            if elem.text is not None:
                self.item = elem.text
                yield self.item
            if len(elem):
                self.size = len(elem)
                for self.position, self.item in enumerate(elem):
                    for _descendant in _iter_descendants():
                        yield _descendant

        status = self.item, self.size, self.position, self._iterator
        self._iterator = self.iter_descendants

        if item is not None:
            self.item = item

        if self.item is None:
            self.size, self.position = 1, 0
            yield self.root
            self.item = self.root.getroot() if is_document_node(self.root) else self.root
        elif not is_etree_element(self.item):
            return

        for descendant in _iter_descendants():
            yield descendant

        self.item, self.size, self.position, self._iterator = status

    def iter_children(self, item=None):
        status = self.item, self.size, self.position, self._iterator
        self._iterator = self.iter_children

        if item is not None:
            self.item = item

        if self.item is None:
            self.size, self.position = 1, 0
            self.item = self.root.getroot() if is_document_node(self.root) else self.root
            yield self.item
        elif is_element_node(self.item):
            elem = self.item
            if elem.text is not None:
                self.item = elem.text
                yield self.item
            self.size = len(elem)
            for self.position, self.item in enumerate(elem):
                yield self.item

        self.item, self.size, self.position, self._iterator = status

    def iter_ancestors(self, item=None):
        status = self.item, self.size, self.position, self._iterator
        self._iterator = self.iter_ancestors

        if item is not None:
            self.item = item

        if not is_etree_element(self.item):
            return
        elem = self.item
        parent_map = self.parent_map
        while True:
            try:
                parent = parent_map[self.item]
            except KeyError:
                break
            else:
                if parent is elem:
                    raise ElementPathValueError("not an Element tree, circularity found for %r." % elem)
                self.item = parent
                yield self.item

        self.item, self.size, self.position, self._iterator = status