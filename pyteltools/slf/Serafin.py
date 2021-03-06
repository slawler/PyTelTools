"""!
Read/Write Serafin files and manipulate associated data.

Handles Serafin file with:
  - single and double precision
  - big and little endian

The sizes (file, header, frame) are in bytes (8 bits).
"""

import copy
import numpy as np
import os
import struct

from pyteltools.slf.variable.variables_2d import VARIABLES_2D
from pyteltools.slf.variable.variables_3d import VARIABLES_3D

from .util import logger


# Encoding Information Type (EIT) for Serafin title, variable names and units
SLF_EIT = 'iso-8859-1'


VARIABLES_ID_2D, VARIABLES_ID_3D = {'fr': {}, 'en': {}}, {'fr': {}, 'en': {}}


def build_variables_table():
    base_folder = os.path.dirname(os.path.realpath(__file__))
    for dic, name in zip([VARIABLES_ID_2D, VARIABLES_ID_3D], ['Serafin_var2D.csv', 'Serafin_var3D.csv']):
        with open(os.path.join(base_folder, 'data', name), 'r') as f:
            f.readline()  # header
            for line in f.readlines():
                var_id, var_name_fr, var_name_en, _ = line.rstrip().split(';')
                dic['fr'][var_name_fr] = var_id
                dic['en'][var_name_en] = var_id

build_variables_table()


class SerafinValidationError(Exception):
    """!
    @brief Custom exception for Serafin file content check
    """
    def __init__(self, message):
        """!
        @param message <str>: error message description
        """
        super().__init__(message)
        self.message = message
        logger.error('SERAFIN VALIDATION ERROR: %s' % message)


class SerafinRequestError(Exception):
    """!
    @brief Custom exception for requesting invalid values from Serafin object
    """
    def __init__(self, message):
        """!
        @param message <str>: error message description
        """
        super().__init__(message)
        self.message = message
        logger.error('SERAFIN REQUEST ERROR: %s' % message)


class SerafinHeader:
    """!
    @brief A data type for reading and storing the Serafin file header
    """

    def __init__(self, file, file_size, language):
        """!
        @param file <_io.BufferedReader>: input Serafin stream
        @param file_size <int>: file size (in bytes)
        @param language <str>: Serafin variable name language ('fr' or 'en')
        """
        self.file_size = file_size
        if language not in ('fr', 'en'):
            raise SerafinRequestError('Language (for Serafin variables) %s is not implemented' % language)
        self.language = language

        # Header and frame sizes are set afterwards by specific methods
        self.header_size = -1
        self.frame_size = -1

        # Check if file is empty (usefull if re-runs after a crash)
        if self.file_size == 0:
            raise SerafinValidationError('File is empty (file size is equal to 0)')

        # Determine binary file endianness from first integer
        # File endianness <str>: '>' = big-endian, '<' = little-endian
        self.endian = None
        first_int_block = file.read(4)
        if struct.unpack('>i', first_int_block)[0] == 80:
            self.endian = '>'
        elif struct.unpack('<i', first_int_block)[0] == 80:
            self.endian = '<'
        else:
            raise SerafinValidationError('File endianness could not be determined')
        logger.debug('File is determined to be %s endian' % ('big' if self.endian == '>' else 'litte'))

        # Read title
        self.title = file.read(72)
        self.file_type = file.read(8)
        file.read(4)

        try:
            data_format = self.file_type.decode(SLF_EIT)
            logger.debug('The file type is: "%s"' % data_format)
        except UnicodeDecodeError:
            raise SerafinValidationError('File type is unreadable: %s' % self.file_type)

        if data_format in ('SERAFIND', '       D'):
            self._set_as_double_precision()
        else:
            if data_format not in ('SERAFIN ', 'SERAPHIN', '        '):
                logger.warning('Format "%s" is unknown and is forced to "SERAFIN"' % data_format)
                self.file_type = bytes('SERAFIN', SLF_EIT).ljust(8)
            self._set_as_single_precision()

        # Read the number of linear and quadratic variables
        file.read(4)
        self.nb_var = self.unpack_int(file.read(4), 1)[0]
        self.nb_var_quadratic = self.unpack_int(file.read(4), 1)[0]
        logger.debug('The file has %d variables' % self.nb_var)
        file.read(4)
        if self.nb_var_quadratic != 0:
            raise SerafinValidationError('The number of quadratic variables is not equal to zero')

        # Read variable names and units
        self.var_IDs, self.var_names, self.var_units = [], [], []
        for ivar in range(self.nb_var):
            file.read(4)
            self.var_names.append(file.read(16))
            self.var_units.append(file.read(16))
            file.read(4)

        # IPARAM: 10 integers (not all are useful...)
        file.read(4)
        self.params = self.unpack_int(file.read(40), 10)
        file.read(4)
        self.nb_planes = self.params[6]

        self.is_2d = (self.nb_planes == 0)

        if self.params[-1] == 1:
            # Read 6 integers which correspond to simulation starting date
            file.read(4)
            self.date = self.unpack_int(file.read(6 * 4), 6)
            file.read(4)
        else:
            self.date = None

        self.has_knolg = (self.params[7] != 0 or self.params[8] != 0)

        # 4 very important integers
        file.read(4)
        self.nb_elements = self.unpack_int(file.read(4), 1)[0]
        self.nb_nodes = self.unpack_int(file.read(4), 1)[0]
        self.nb_nodes_per_elem = self.unpack_int(file.read(4), 1)[0]
        test_value = self.unpack_int(file.read(4), 1)[0]
        if test_value != 1:
            raise SerafinValidationError('The magic number is not equal to one')
        file.read(4)

        # verify data consistence and determine 2D or 3D
        if self.is_2d:
            if self.nb_nodes_per_elem != 3:
                raise SerafinValidationError('Unknown mesh type')
        else:
            if self.nb_nodes_per_elem != 6:
                raise SerafinValidationError('The number of nodes per element is not equal to 6')
            if self.nb_planes < 2:
                raise SerafinValidationError('The number of planes is less than 2')
        logger.debug('The file is determined to be %s' % {True: '2D', False: '3D'}[self.is_2d])

        # determine the number of nodes in 2D
        if self.is_2d:
            self.nb_nodes_2d = self.nb_nodes
        else:
            self.nb_nodes_2d = self.nb_nodes // self.nb_planes

        # IKLE
        file.read(4)
        nb_ikle_values = self.nb_elements * self.nb_nodes_per_elem
        self.ikle = np.array(self.unpack_int(file.read(4 * nb_ikle_values), nb_ikle_values))
        file.read(4)

        # IPOBO
        file.read(4)
        self.ipobo = np.array(self.unpack_int(file.read(4 * self.nb_nodes), self.nb_nodes))
        file.read(4)

        # x coordinates
        file.read(4)
        coord_size = self.nb_nodes * self.float_size
        self.x = np.array(self.unpack_float(file.read(coord_size), self.nb_nodes), dtype=self.np_float_type)
        file.read(4)

        # y coordinates
        file.read(4)
        self.y = np.array(self.unpack_float(file.read(coord_size), self.nb_nodes), dtype=self.np_float_type)
        file.read(4)

        # Compute and set header and frame sizes
        self._set_header_size()
        self._set_frame_size()

        # Deduce the number of frames and test the integer division
        self.nb_frames = (self.file_size - self.header_size) // self.frame_size
        logger.debug('The file has %d frames of size %d bytes' % (self.nb_frames, self.frame_size))

        diff_size = self.file_size - self.header_size - self.frame_size * self.nb_frames
        # A difference of only one byte is tolerated.
        # Indeed some old files may contain an ending \x0A character (Linux line feed)
        if diff_size != 0 and diff_size != 1:
            raise SerafinValidationError('Something wrong with the file size (header and frames). '
                                         'File is probably corrupted, difference of %i bytes' % diff_size)

        # Deduce variable IDs from names
        var_table = VARIABLES_ID_2D[self.language] if self.is_2d else VARIABLES_ID_3D[self.language]
        for var_name, var_unit in zip(self.var_names, self.var_units):
            name = var_name.decode(encoding=SLF_EIT).strip()
            if name not in var_table:
                slf_type = '2D' if self.is_2d else '3D'
                logger.warning('WARNING: The %s variable name "%s" is not known (lang=%s). '
                                      'The complete name will be used as ID' % (slf_type, name, self.language))
                var_id = name
            else:
                var_id = var_table[name]
            self.var_IDs.append(var_id)

        # Build ikle2d
        if not self.is_2d:
            ikle = self.ikle.reshape(self.nb_elements, self.nb_nodes_per_elem)
            self.ikle_2d = np.empty([self.nb_elements // (self.nb_planes - 1), 3], dtype=int)
            nb_lines = self.ikle_2d.shape[0]
            # test the integer division
            if nb_lines * (self.nb_planes - 1) != self.nb_elements:
                raise SerafinValidationError('The number of elements is not divisible by (number of planes - 1)')
            for i in range(nb_lines):
                self.ikle_2d[i] = ikle[i, [0, 1, 2]]
        else:
            self.ikle_2d = self.ikle.reshape(self.nb_elements, self.nb_nodes_per_elem)

        logger.debug('Finished reading the header')

    def _set_as_single_precision(self):
        """Set Serafin as single precision"""
        self.float_type = 'f'
        self.float_size = 4
        self.np_float_type = np.float32

    def _set_as_double_precision(self):
        """Set Serafin as double precision"""
        self.float_type = 'd'
        self.float_size = 8
        self.np_float_type = np.float64

    def unpack_int(self, bytes2unpack, nb=1):
        """
        Unpack bytes to integer(s)
        @param bytes2unpack <bytes>: bytes to unpack
        @param nb <int>: number of consecutive integers
        @return <(int)>
        """
        fmt = self.endian + str(nb) + 'i'
        return struct.unpack(fmt, bytes2unpack)

    def unpack_float(self, bytes2unpack, nb=1):
        """
        Unpack bytes to float(s)
        @param bytes2unpack <bytes>: bytes to unpack
        @param nb <int>: number of consecutive float
        @return <(float)>
        """
        fmt = self.endian + str(nb) + self.float_type
        return struct.unpack(fmt, bytes2unpack)

    def pack_int(self, *args, nb=1):
        """
        Pack integers
        @param args <(int)>: integers to pack
        @param nb <int>: number of integer values
        @return <bytes>
        """
        fmt = self.endian + str(nb) + 'i'
        return struct.pack(fmt, *args)

    def pack_float(self, *args, nb=1):
        """
        Pack floats
        @param args <(float)>: floats to pack
        @param nb <int>: number of float values
        @return <bytes>
        """
        fmt = self.endian + str(nb) + self.float_type
        return struct.pack(fmt, *args)

    def toggle_endianness(self):
        """Toggle original endianness (between big or little endian)"""
        if self.endian == '>':
            self.endian = '<'
        else:
            self.endian = '>'
        logger.debug('Toggle endianness to %s' % ('big' if self.endian == '>' else 'litte'))

    def _set_header_size(self):
        """Set header size"""
        nb_ikle_values = self.nb_elements * self.nb_nodes_per_elem
        coord_size = self.nb_nodes * self.float_size
        self.header_size = (80 + 8) + (8 + 8) + (self.nb_var * (8 + 32)) \
                                    + (40 + 8) + (self.params[-1] * ((6 * 4) + 8)) + (16 + 8) \
                                    + (nb_ikle_values * 4 + 8) + (self.nb_nodes * 4 + 8) + 2 * (coord_size + 8)

    def _set_frame_size(self):
        """Set frame size (all variable values for one time step)"""
        self.frame_size = 8 + self.float_size + (self.nb_var * (8 + self.nb_nodes * self.float_size))

    def _expected_file_size(self):
        """Returns expected file size"""
        return self.header_size + self.nb_frames * self.frame_size

    def summary(self):
        template = 'The file is of type {} {}. It has {} variable{}{},\n' \
                   'on {} nodes and {} elements for {} time frame{}.'
        return template.format(self.file_type.decode(SLF_EIT),
                               {True: '2D', False: '3D'}[self.is_2d], self.nb_var,
                               ['', 's'][self.nb_var > 1], '' if self.is_2d else ', %d layers' % self.nb_planes,
                               self.nb_nodes, self.nb_elements, self.nb_frames,
                               ['', 's'][self.nb_frames > 1])

    def copy(self):
        """Returns a deep copy of the current instance"""
        return copy.deepcopy(self)

    def copy_as_2d(self):
        """Returns a 2D equivalent copy of the current instance"""
        if self.is_2d:
            raise SerafinRequestError('Cannot convert header to a 2D equivalent because the input is not 3D')

        ori_params = self.params
        nb_planes = self.nb_planes
        new_header = self.copy()

        # Overwrites relevant attributes
        new_header.nb_planes = 0
        new_params = list(ori_params)
        new_params[6] = new_header.nb_planes
        new_header.params = tuple(new_params)
        new_header.is_2d = True
        new_header.nb_elements //= (nb_planes - 1)
        new_header.nb_nodes //= nb_planes
        new_header.nb_nodes_per_elem = 3
        new_header.nb_nodes_2d = new_header.nb_nodes
        npd = new_header.nb_nodes_per_elem
        new_header.ikle = np.take(self.ikle, [2*npd*i+j for i in range(new_header.nb_elements) for j in range(3)])
        new_header.ipobo = self.ipobo[:self.nb_nodes_2d]
        new_header.x = self.x[:self.nb_nodes_2d]
        new_header.y = self.y[:self.nb_nodes_2d]

        # Update sizes
        new_header._set_header_size()
        new_header._set_frame_size()
        new_header.file_size = new_header._expected_file_size()

        return new_header

    def is_double_precision(self):
        return self.float_type == 'd'

    def to_single_precision(self):
        self.file_type = bytes('SERAFIN', SLF_EIT).ljust(8)
        self.float_type = 'f'
        self.float_size = 4
        self.np_float_type = np.float32
        self._set_frame_size()
        self._set_header_size()
        self.file_size = self._expected_file_size()

    def empty_variables(self):
        """Empty all variables"""
        self.nb_var = 0
        self.var_IDs, self.var_names, self.var_units = [], [], []

    def add_variable(self, var_ID, var_name, var_unit):
        """!
        @brief: Add a single variable
        @param var_ID <str>: variables identifier (abbreviation)
        @param var_name <bytes>: variable name
        @param var_unit <bytes>: variable unit
        """
        self.nb_var += 1
        self.var_IDs.append(var_ID)
        self.var_names.append(var_name)
        self.var_units.append(var_unit)

    def add_variable_str(self, var_ID, var_name, var_unit):
        """!
        @brief: Add a single variable with strings
        @param var_ID <str>: variables identifier (abbreviation)
        @param var_name <str>: variable name
        @param var_unit <str>: variable unit
        """
        self.nb_var += 1
        self.var_IDs.append(var_ID)
        self.var_names.append(bytes(var_name, SLF_EIT).ljust(16))
        self.var_units.append(bytes(var_unit, SLF_EIT).ljust(16))

    def add_variable_from_ID(self, var_ID):
        """!
        @brief: Add new variable from its ID
        @param var_ID <str>: variable ID
        """
        try:
            if self.is_2d:
                var_name = VARIABLES_2D[var_ID].name(self.language)
                var_unit = VARIABLES_2D[var_ID].unit()
            else:
                var_name = VARIABLES_3D[var_ID].name(self.language)
                var_unit = VARIABLES_3D[var_ID].unit()
        except KeyError:
            raise SerafinRequestError('The variable "%s" is not known (lang=%s)' % (var_ID, self.language))
        self.add_variable_str(var_ID, var_name, var_unit)

    def set_variables(self, selected_vars):
        """!
        @brief: Set new variables
        @param selected_vars <[str, bytes, bytes]>: list composed of variable ID, name and unit
        """
        self.empty_variables()
        for var_ID, var_name, var_unit in selected_vars:
            self.add_variable(var_ID, var_name, var_unit)

    def transform_mesh_copy(self, transformations):
        """!
        @brief Apply transformations on 2D nodes of a mesh copy
        @param transformations <[geom.transformation.Transformation]>: list of successive transformations
        @return: modified copy of original header with transformed mesh nodes
        """
        if not transformations:
            return self.copy()
        points = [np.array([x, y, 0]) for x, y in zip(self.x, self.y)]
        for t in transformations:
            points = [t(p) for p in points]
        new_header = self.copy()
        new_header.x = np.array([p[0] for p in points])
        new_header.y = np.array([p[1] for p in points])
        return new_header

    def transform_mesh(self, transformations):
        """!
        @brief Apply transformations on mesh nodes (only in 2D)
        @param transformations <[geom.transformation.Transformation]>: list of successive transformations
        """
        if not transformations:
            return
        points = [np.array([x, y, 0]) for x, y in zip(self.x, self.y)]
        for t in transformations:
            points = [t(p) for p in points]
        self.x = np.array([p[0] for p in points])
        self.y = np.array([p[1] for p in points])


class Serafin:
    """!
    @brief A Serafin object corresponds to a single Serafin in file IO stream
    """
    def __init__(self, filename, mode, language):
        self.language = language
        self.mode = mode

        self.file = None  # will be opened when called using 'with'
        self.filename = filename
        self.file_size = None
        self.mode = mode

    def __enter__(self):
        self.file = open(self.filename, self.mode)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.file.close()
        return False


class Read(Serafin):
    """!
    @brief Serafin file input stream
    """
    def __init__(self, filename, language):
        """!
        @param filename <str>: path to input Serafin file
        @param language <str>: Serafin variable name language ('fr' or 'en')
        """
        super().__init__(filename, 'rb', language)
        self.header = None
        self.time = []
        self.file_size = os.path.getsize(self.filename)
        logger.info('Reading the input file: "%s" of size %d bytes' % (filename, self.file_size))

    def read_header(self):
        """!
        @brief Read the file header and check the file consistency
        """
        self.header = SerafinHeader(self.file, self.file_size, self.language)

    def get_time(self):
        """!
        @brief Read the time in the Serafin file
        """
        if self.header is None:
            raise SerafinRequestError('Cannot read time without any header (forgot read_header ?)')
        logger.debug('Reading the time series from the file')
        self.file.seek(self.header.header_size, 0)
        for i in range(self.header.nb_frames):
            self.file.read(4)
            self.time.append(self.header.unpack_float(self.file.read(self.header.float_size), 1)[0])
            self.file.read(4)
            self.file.seek(self.header.frame_size - 8 - self.header.float_size, 1)

    def subset_time(self, start, end, ech):
        """!
        Get a subset of the time frames list
        @param start <float>: starting time (in seconds)
        @param end <float>: ending time (in seconds)
        @param ech <int>: sampling frequency
        @return <[float]>: sampled time serie
        """
        new_time = []
        for time_index, time in enumerate(self.time):
            if start <= time <= end:
                if time_index % ech == 0:
                    new_time.append((time_index, time))
        return new_time

    def _get_var_index(self, var_ID):
        """!
        @brief Handle data request by variable ID
        @param var_ID <str>: the ID of the requested variable
        @return index <int> the index of the frame (0-based)
        """
        if self.header is None:
            raise SerafinRequestError('Cannot extract variable from empty list (forgot read_header ?)')
        try:
            index = self.header.var_IDs.index(var_ID)
        except ValueError:
            raise SerafinRequestError('Variable ID %s not found' % var_ID)
        return index

    def read_var_in_frame(self, time_index, var_ID):
        """!
        @brief Read a single variable in a frame
        @param time_index <int>: the index of the frame (0-based)
        @param var_ID <str>: variable ID
        @return <numpy 1D-array>: values of the variables, of length equal to the number of nodes
        """
        if time_index < 0:
            raise SerafinRequestError('Impossible to read a negative time index!')
        logger.debug('Reading variable %s at frame %i' % (var_ID, time_index))
        pos_var = self._get_var_index(var_ID)
        self.file.seek(self.header.header_size + time_index * self.header.frame_size
                       + 8 + self.header.float_size + pos_var * (8 + self.header.float_size * self.header.nb_nodes), 0)
        self.file.read(4)
        return np.array(self.header.unpack_float(self.file.read(self.header.float_size * self.header.nb_nodes),
                                                 self.header.nb_nodes), dtype=self.header.np_float_type)

    def read_var_in_frame_as_3d(self, time_index, var_ID):
        """!
        @brief Read a single variable in a 3D frame
        @param time_index <int>: the index of the frame (0-based)
        @param var_ID <str>: variable ID
        @return <numpy 2D-array>: values of the variables with shape (planes number, number of 2D nodes)
        """
        if self.header.is_2d:
            raise SerafinRequestError('Reading values as 3D is only possible in 3D!')
        new_shape = (self.header.nb_planes, self.header.nb_nodes_2d)
        return self.read_var_in_frame(time_index, var_ID).reshape(new_shape)

    def read_var_in_frame_at_layer(self, time_index, var_ID, iplan):
        """!
        @brief Read a single variable in a frame at specific layer
        @param time_index <int>: the index of the frame (0-based)
        @param var_ID <str>: variable ID
        @param iplan <int>: 1-based index of layer
        @return <numpy 1D-array>: values of the variables, of length equal to the number of nodes
        """
        if self.header.is_2d:
            raise SerafinRequestError('Extracting values at a specific layer is only possible in 3D!')
        if iplan < 1 or iplan > self.header.nb_planes:
            raise SerafinRequestError('Layer %i is not inside [1, %i]' % (iplan, self.header.nb_planes))
        return self.read_var_in_frame_as_3d(time_index, var_ID)[iplan + 1]


class Write(Serafin):
    """!
    @brief Serafin file output stream
    """
    def __init__(self, filename, language, overwrite=False):
        """!
        @param filename <str>: path to output Serafin file
        @param language <str>: Serafin variable name language ('fr' or 'en')
        @param overwrite <bool>: overwrite if file already exists
        """
        mode = 'wb' if overwrite else 'xb'
        super().__init__(filename, mode, language)
        logger.info('Writing the output file: "%s"' % filename)

    def __enter__(self):
        try:
            return Serafin.__enter__(self)
        except FileExistsError:
            raise SerafinRequestError('Cannot overwrite existing file')

    def write_header(self, header):
        """!
        @brief Write Serafin header from attributes
        """
        logger.debug('Writing header with {} nodes, {} elements{} and {} variable{}'.format(header.nb_nodes,
            header.nb_elements, '' if header.is_2d else ', %i layers' % header.nb_planes,
            header.nb_var, ['', 's'][header.nb_var > 1]))
        # Title and file type
        self.file.write(header.pack_int(80))
        self.file.write(header.title)
        self.file.write(header.file_type)
        self.file.write(header.pack_int(80))

        # Number of variables
        self.file.write(header.pack_int(8))
        self.file.write(header.pack_int(header.nb_var))
        self.file.write(header.pack_int(header.nb_var_quadratic))
        self.file.write(header.pack_int(8))

        # Variable names and units
        for j in range(header.nb_var):
            self.file.write(header.pack_int(2 * 16))
            self.file.write(header.var_names[j].ljust(16))
            self.file.write(header.var_units[j].ljust(16))
            self.file.write(header.pack_int(2 * 16))

        # Date
        self.file.write(header.pack_int(10 * 4))
        self.file.write(header.pack_int(*header.params, nb=10))
        self.file.write(header.pack_int(10 * 4))
        if header.params[-1] == 1:
            self.file.write(header.pack_int(6 * 4))
            self.file.write(header.pack_int(*header.date, nb=6))
            self.file.write(header.pack_int(6 * 4))

        # Number of elements, of nodes, of nodes per element and the magic number
        self.file.write(header.pack_int(4 * 4))
        self.file.write(header.pack_int(header.nb_elements))
        self.file.write(header.pack_int(header.nb_nodes))
        self.file.write(header.pack_int(header.nb_nodes_per_elem))
        self.file.write(header.pack_int(1))  # magic number
        self.file.write(header.pack_int(4 * 4))

        # IKLE
        nb_ikle_values = header.nb_elements * header.nb_nodes_per_elem
        self.file.write(header.pack_int(4 * nb_ikle_values))
        self.file.write(header.pack_int(*header.ikle, nb=nb_ikle_values))
        self.file.write(header.pack_int(4 * nb_ikle_values))

        # IPOBO
        self.file.write(header.pack_int(4 * header.nb_nodes))
        self.file.write(header.pack_int(*header.ipobo, nb=header.nb_nodes))
        self.file.write(header.pack_int(4 * header.nb_nodes))

        # X coordinates
        self.file.write(header.pack_int(header.float_size * header.nb_nodes))
        self.file.write(header.pack_float(*header.x, nb=header.nb_nodes))
        self.file.write(header.pack_int(header.float_size * header.nb_nodes))

        # Y coordinates
        self.file.write(header.pack_int(header.float_size * header.nb_nodes))
        self.file.write(header.pack_float(*header.y, nb=header.nb_nodes))
        self.file.write(header.pack_int(header.float_size * header.nb_nodes))

    def write_entire_frame(self, header, time_to_write, values):
        """!
        @brief write all variables/nodes values
        @param header <SerafinHeader>: output header
        @param time_to_write <float>: output time (in seconds)
        @param values <numpy 2D-array>: values to write, of dimension (nb_var, nb_nodes)
        """
        self.file.write(header.pack_int(header.float_size))
        self.file.write(header.pack_float(time_to_write))
        self.file.write(header.pack_int(header.float_size))

        for i in range(header.nb_var):
            self.file.write(header.pack_int(header.float_size * header.nb_nodes))
            self.file.write(header.pack_float(*values[i, :], nb=header.nb_nodes))
            self.file.write(header.pack_int(header.float_size * header.nb_nodes))

