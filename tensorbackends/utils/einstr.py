"""
This module implements einstr utilities.
"""

import re, string, itertools, functools, operator


chars = string.ascii_letters


def parse(subscripts):
    return Expression.parse(subscripts)


def parse_einsum(subscripts, ndims):
    expr = parse(subscripts).match(ndims)
    if len(expr.outputs) != 1:
        raise ValueError('too many outputs for einsum: {}'.format(expr.source))
    output_indices = expr.output_indices
    if not output_indices.issubset(expr.input_indices):
        raise ValueError('output indices should be a subset of input indices for einsum: "{}"'.format(expr.source))
    return expr


def parse_einsvd(subscripts, ndim):
    expr = parse(subscripts).match([ndim])
    if len(expr.inputs) != 1:
        raise ValueError('expect one input for einsvd: "{}"'.format(expr.source))
    if len(expr.outputs) != 2:
        raise ValueError('expect two outputs for einsvd: "{}"'.format(expr.source))
    if len(set(expr.inputs[0])) != len(expr.inputs[0]):
        raise ValueError('input indices should not repeat for einsvd: "{}"'.format(expr.source))
    input_indices, output_indices = expr.input_indices, expr.output_indices
    if not input_indices.issubset(output_indices):
        raise ValueError('expect input indices subset of output indices for einsvd: "{}"'.format(expr.source))
    newindices = output_indices - input_indices
    if len(newindices) != 1:
        raise ValueError('expect one new index in outputs for einsvd: "{}"'.format(expr.source))
    newindex = newindices.pop()
    if newindex not in expr.outputs[0] or newindex not in expr.outputs[1]:
        raise ValueError('expect new index in both outputs for einsvd: "{}"'.format(expr.source))
    if len(expr.outputs[0]) == 1 or len(expr.outputs[1]) == 1:
        raise ValueError('expect outputs to be at least two dimensional for einsvd: "{}"'.format(expr.source))
    if len(output_indices) != len(expr.outputs[0]) + len(expr.outputs[1]) - 1:
        raise ValueError('only the new index can repeat in the output for einsvd: "{}"'.format(expr.source))
    return expr


def parse_einsumsvd(subscripts, ndims):
    expr = parse(subscripts).match(ndims)
    if len(expr.inputs) < 1:
        raise ValueError('expect at least one input for einsumsvd: "{}"'.format(expr.source))
    if len(expr.outputs) != 2:
        raise ValueError('expect two outputs for einsumsvd: "{}"'.format(expr.source))
    input_indices, output_indices = expr.input_indices, expr.output_indices
    newindices = output_indices - input_indices
    if len(newindices) != 1:
        raise ValueError('expect one new index in outputs for einsumsvd: "{}"'.format(expr.source))
    newindex = newindices.pop()
    if newindex not in expr.outputs[0] or newindex not in expr.outputs[1]:
        raise ValueError('expect new index in both outputs for einsumsvd: "{}"'.format(expr.source))
    if len(expr.outputs[0]) == 1 or len(expr.outputs[1]) == 1:
        raise ValueError('expect outputs to be at least two dimensional for einsumsvd: "{}"'.format(expr.source))
    return expr


def split_einsumsvd(expr):
    newindex = (expr.output_indices - expr.input_indices).pop()
    intermediate_indices = [
        idx for idx in itertools.chain.from_iterable(expr.outputs)
        if idx != newindex
    ]
    output_intermediate = [OutputTerm(intermediate_indices, [], '')]
    input_intermediate = [InputTerm(intermediate_indices, '')]
    # TODO here expr.nindices > number of distinct indices in subexpressions
    einsum_expr = Expression(expr.inputs, output_intermediate, source=expr.source)
    einsvd_expr = Expression(input_intermediate, expr.outputs, source=expr.source)
    return einsum_expr, einsvd_expr


class Expression:
    def __init__(self, inputs, outputs, source=''):
        self.inputs = inputs
        self.outputs = outputs
        self.source = source

    @property
    def nindices(self):
        return max(
            idx for idx in itertools.chain(self.input_indices, self.output_indices)
            if idx is not Ellipsis
        ) + 1

    @property
    def indices_string(self):
        inputs = ','.join(t.indices_string for t in self.inputs)
        outputs = ','.join(t.indices_string for t in self.outputs)
        return '{}->{}'.format(inputs, outputs)

    @property
    def input_indices(self):
        return set(itertools.chain.from_iterable(self.inputs))

    @property
    def output_indices(self):
        return set(itertools.chain.from_iterable(self.outputs))

    @staticmethod
    def parse(subscripts):
        subscripts = re.sub(r'\s', '', subscripts)
        inputs_outputs = subscripts.split('->')
        if len(inputs_outputs) != 2:
            raise ValueError('invalid subscripts: "{}"'.format(subscripts))
        mapping = {}
        input_subscripts = inputs_outputs[0].split(',')
        output_subscripts = inputs_outputs[1].split(',')
        inputs = [InputTerm.parse(s, mapping) for s in input_subscripts]
        outputs = [OutputTerm.parse(s, mapping) for s in output_subscripts]
        if len(mapping) > len(chars):
            raise ValueError('too many indices: {} (maximum {})'.format(len(mapping), len(chars)))
        return Expression(inputs, outputs, source=subscripts)

    def match(self, ndims):
        if len(ndims) != len(self.inputs):
            raise ValueError('number of operands does not match subscripts "{}": {}'.format(self.source, len(ndims)))
        nindices = self.nindices
        def fresh():
            nonlocal nindices
            newindex = nindices
            nindices += 1
            return newindex
        newinputs = [t.match(ndim, fresh) for t, ndim in zip(self.inputs, ndims)]
        ellipsis = list(range(self.nindices, nindices))
        newoutputs = [t.expand(ellipsis) for t in self.outputs]
        return Expression(newinputs, newoutputs, source=self.source)

    def __str__(self):
        inputs = ','.join(str(t) for t in self.inputs)
        outputs = ','.join(str(t) for t in self.outputs)
        return '{}->{}'.format(inputs, outputs)

    def __repr__(self):
        return "Expression('{}')".format(str(self))


class InputTerm:
    def __init__(self, indices, source):
        self.indices = indices
        self.source = source

    @property
    def indices_string(self):
        return ''.join('...' if idx is Ellipsis else chars[idx] for idx in self.indices)

    def __len__(self):
        return len(self.indices)

    def __iter__(self):
        yield from self.indices

    def find(self, index):
        return next((i for i, idx in enumerate(self.indices) if idx == index), None)

    @staticmethod
    def parse(subscripts, mapping):
        indices = []
        found_ellipsis = False
        i = 0
        while i < len(subscripts):
            if subscripts[i:].startswith('...'):
                if found_ellipsis:
                    raise ValueError('each term can contain at most one ellipsis')
                found_ellipsis = True
                indices.append(Ellipsis)
                i += 3
            elif subscripts[i] in '()':
                raise ValueError('indices fusing is not allowed in input subscripts: "{}"'.format(subscripts))
            else:
                indices.append(mapping.setdefault(subscripts[i], len(mapping)))
                i += 1
        return InputTerm(indices, subscripts)

    def match(self, ndim, fresh):
        newindices = []
        i = 0
        for j, idx in enumerate(self.indices):
            if idx is Ellipsis:
                count = (ndim - i) - (len(self) - j - 1)
                if count < 0:
                    raise ValueError('indices "{}" do not match ndim: {}'.format(self.source, ndim))
                newindices.extend(fresh() for _ in range(count))
                i += count
            else:
                newindices.append(idx)
                i += 1
        if i != ndim:
            raise ValueError('indices "{}" do not match ndim: {}'.format(self.source, ndim))
        return InputTerm(newindices, self.source)

    def __str__(self):
        return ''.join('...' if idx is Ellipsis else chars[idx] for idx in self.indices)

    def __repr__(self):
        return "InputTerm('{}')".format(str(self))


class OutputTerm:
    def __init__(self, indices, fusing, source):
        self.indices = indices
        self.fusing = fusing
        self.source = source

    @property
    def indices_string(self):
        return ''.join('...' if idx is Ellipsis else chars[idx] for idx in self.indices)

    def __len__(self):
        return len(self.indices)

    def __iter__(self):
        yield from self.indices

    def find(self, index):
        return next((i for i, idx in enumerate(self.indices) if idx == index), None)

    @staticmethod
    def parse(subscripts, mapping):
        indices = []
        fusing = []
        i = 0
        found_ellipsis = False
        start = None
        while i < len(subscripts):
            if subscripts[i:].startswith('...'):
                if found_ellipsis:
                    raise ValueError('each term can contain at most one ellipsis')
                found_ellipsis = True
                indices.append(Ellipsis)
                i += 3
            elif subscripts[i] == '(':
                if start is not None:
                    raise ValueError('nested parentheses are not allowed: "{}"'.format(subscripts))
                start = len(indices)
                i += 1
            elif subscripts[i] == ')':
                if start is None:
                    raise ValueError('unmatched parentheses: "{}"'.format(subscripts))
                end = len(indices)
                fusing.append((start, len(indices)))
                start = None
                i += 1
            else:
                indices.append(mapping.setdefault(subscripts[i], len(mapping)))
                i += 1
        if start is not None:
            raise ValueError('unmatched parentheses: "{}"'.format(subscripts))
        return OutputTerm(indices, fusing, subscripts)

    def expand(self, ellipsis):
        newindices = []
        ellipsis_position = -1
        for i, idx in enumerate(self.indices):
            if idx is Ellipsis:
                newindices.extend(ellipsis)
                ellipsis_position = i
            else:
                newindices.append(idx)
        if ellipsis and ellipsis_position < 0:
            raise ValueError('expect ellipsis in output subscripts: "{}"'.format(self.source))
        if ellipsis_position < 0:
            newfusing = list(self.fusing)
        else:
            pad = lambda j: (j + len(ellipsis) - 1) if j > ellipsis_position else j
            newfusing = [(pad(start), pad(end)) for start, end in self.fusing]
        return OutputTerm(newindices, newfusing, self.source)

    def newshape(self, shape):
        if Ellipsis in self.indices:
            raise ValueError('ellipsis not expanded')
        if len(shape) != len(self):
            raise ValueError('indices "{}" do not match shape: {}'.format(str(self), shape))
        newshape = []
        i = 0
        for start, end in self.fusing:
            newshape.extend(shape[i:start])
            newshape.append(functools.reduce(operator.mul, shape[start:end], 1))
            i = end
        newshape.extend(shape[i:])
        return tuple(newshape)

    def __str__(self):
        result = []
        asstr = lambda idx: '...' if idx is Ellipsis else chars[idx]
        i = 0
        for start, end in self.fusing:
            result.extend(asstr(idx) for idx in self.indices[i:start])
            result.append('(')
            result.extend(asstr(idx) for idx in self.indices[start:end])
            result.append(')')
            i = end
        result.extend(asstr(idx) for idx in self.indices[i:])
        return ''.join(result)

    def __repr__(self):
        return "OutputTerm('{}')".format(str(self))
