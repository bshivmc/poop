import os
import sys
from itertools import groupby
from operator import itemgetter
from optparse import OptionParser, OptionValueError

# Generate Documentation
# epydoc -v --docformat restructuredtext poop.py

__all__ = ('PoopJob', 'PoopRunner', 'run', 'getparser')
__doc__ = '''P(ython Had)oop Streaming framework.

The poop module implements all the boring plumbing (pun intended) and
boilerplate code required to implement and invoke Hadoop streaming jobs written
in Python.  It is inspired by Klaas Bosteels' excellent ``dumbo`` [1]_ module
but with a more declarative API style.

Here is a sample poop program that counts the occurence of unique words
within an input set::

    import string
    import sys
    from poop import PoopJob, run
    
    class WordCount(PoopJob):
        @staticmethod
        def map(key, val):
            for w in val.split(): yield (w.lower(), 1)
    
        @staticmethod
        def reduce(key, vals):
            yield (key, sum(map(int, vals)))
    
    
    class UniqueCount(PoopJob):
        @staticmethod
        def map(key, val):
            yield ('unique words', 1)

        # re-use the reducer in WordCount
        reduce = staticmethod(WordCount.reduce)
    
    WordCount.child = UniqueCount
    run(sys.argv, WordCount)

.. [1] http://github.com/klbostee/dumbo/
'''

_MAP, _RED = 'MAP', 'REDUCE'


def check_hadoop_home(option, opt_str, value, parser):
    '''Ensure that the Hadoop client/stack exists.

    This check is only performed if the user specifies a location.  The map/red
    functionality can be used without a Hadoop installation.
    '''
    if not os.path.exists(value):
        raise OptionValueError(' '.join(('Hadoop home', value, 'does not exist.')))
    else:
        setattr(parser.values, option.dest, value)


_parser = OptionParser()
_parser.add_option('-i', '--input', dest='inputlist', action='append')
_parser.add_option('-o', '--output', dest='output')
_parser.add_option('-e', '--extra_hadoop_opts', dest='extra_hadoop_opts',
        help='extra hadoop command line arguments to pass',
        default='')
_parser.add_option('-d', '--intermediate_data_dir', dest='int_data_dir',
        help='base directory for intermediate outputs in multi-job lists',
        default='/__poop')
_parser.add_option('-H', '--hadoophome', dest='hadoophome',
        action='callback', callback=check_hadoop_home, type='str',
        help='location of the Hadoop client/stack installation.',
        default='/usr/local/hadoop')
_parser.add_option('-S', '--streaming', dest='streaming', default=None,
        help='location of the Hadoop streaming jar to be used;  by default this program will search for it in HADOOPHOME')
_parser.add_option('-p', '--python', dest='python', default='python',
        help='python command with which to invoke the job on worker nodes')
_parser.add_option('--dryrun', action='store_true', dest='dryrun',
        help='print out job list invocations and exit.')
# Future features:
#_parser.add_option('-D', '--delete_intermediates', action='store_true',
#       help='delete intermediate data directories after run.',
#       dest='del_int_data')
#_parser.add_option('--getouput', dest='getoutput',
#       help='download the output to a file after this task has finished.')


class PoopJob(object):
    "Class defining a map/reduce task."
    def __init__(self, parent=None, input=None, output=None):
        '''Base constructor for PoopJob instances.

        ``parent``
          a parent PoopJob instance (forces ``input`` to ``parent.output``)
        ``input``
          a string or any sequence of strings representing input files/dirs.
        ``output``
          the output directory
        '''
        if parent:
            self.input = parent.output
        else:
            self.input = input
            assert(None != self.input)
        self.output = output

    def name(self):
        return self.__class__.__name__

    def submit(self, argv, opts):
        'Generate the shell command to submit this PoopJob instance.'
        klass = self.__class__

        # here we don't use name() because it might be overridden
        clsname = klass.__name__
        jobsrc = argv[0].split("/")[-1]
        args = []

        # set input/output command-line flags
        if isinstance(self.input, str):
            args += [u'-input', self.input]
        else:
            args += [u'-input %s' % i for i in self.input]
        args.append('-output %s' % self.output)

        args += [
            # -file poop.py (whereever it might be)
            "-file '%s'" % __file__,    
            "-file '%s'" % argv[0],    
            "-mapper '%s %s %s %s'" % (opts.python, jobsrc, _MAP, clsname),
            # strip whitespace from name
            "-jobconf mapred.job.name=%s" % ''.join(self.name().split()),
            opts.extra_hadoop_opts,
        ]

        # extra magic to allow client programs to not specify any reduce
        # function implicitly
        if hasattr(klass, 'reduce'):
            args.append("-reducer '%s %s %s %s'" % (opts.python, jobsrc, _RED, clsname))
        else:
            args.append('-numReduceTasks 0')

        # add any additional flags specified in the class variable 'cli'
        if hasattr(klass, 'cli'):
            conf = klass.cli
            args += [u'-%s "%s"' % (key, unicode(conf[key])) for key in conf]

        # optparse checks for hadoop home, but in case it's not given, we need
        # to check the default:
        if not os.path.exists(opts.hadoophome):
            raise OptionValueError(
                ' '.join(('Hadoop home', opts.hadoophome, 'does not exist.')))
        hadoop = os.path.join(opts.hadoophome, 'bin', 'hadoop')

        if opts.streaming: cmd = [hadoop, 'jar', opts.streaming]
        else: cmd = [hadoop, 'jar', getstreamingjar(opts.hadoophome)]

        return u' '.join(cmd + args)


class PoopRunner(object):
    'Override to implement custom input/output formats.'
    # iter{map,reduce} functions are modified from dumbo package
    # http://github.com/klbostee/dumbo/
    def itermap(self, input, mapper):
        '''Given raw input from ``sys.stdin``, determine how the input is
        passed to the map function.  By default, the ``key`` parameter is set
        to ``None`` and each line is passed as the ``value`` parameter.
        '''
        for i in input: 
            for output in mapper(None, i): yield output

    def iterreduce(self, input, reducer, decoder=None):
        'Group data by key, then pass each group to the reducer function.'
        if decoder: data = decoder(input)
        else: data = input
        for key, values in groupby(data, itemgetter(0)):
            #for output in reducer(key, (v[1] for v in values)):
            for output in reducer(key, (v[1] for v in values)):
                yield output

    def stream_encode(self, stream):
        'Determines how the input to ``iterreduce`` is parsed.'
        for line in stream: yield u"\t".join(map(unicode, line))

    def stream_decode(self, stream):
        'Determines how mapper and reducer is written to ``sys.stdout``.'
        for input in stream: yield input.split(u"\t", 1)


def run(argv, poopklass):
    '''The __main__ logic.

    Client programs call this function in their main [1]_ logic.  This function
    dispatches to the appropriate handler based on command-line arguments.

    .. [1] http://www.artima.com/weblogs/viewpost.jsp?thread=4829
    '''
    op = argv[1]
    klassdict = makeklassdict(poopklass)
    run = getrunner(poopklass)

    if op in (_MAP, _RED):
        jobklass = klassdict[argv[2]]
    if op == _MAP:
        out = run.itermap(sys.stdin, jobklass.map)
        if hasattr(jobklass, 'combiner'):
            out = run.iterreduce(sorted(out), jobklass.combiner)
        out = run.stream_encode(out)
    elif op == _RED:
        out = run.stream_encode(run.iterreduce(sys.stdin, jobklass.reduce, run.stream_decode))
    else:
        return main(argv, poopklass)

    for e in out: print e
    return 0


def main(argv, poopklass):
    'Code only executed by the client requesting the jobs (non-map/red logic).'
    opts, args = _parser.parse_args(argv)
    joblist = makejoblist(poopklass, opts.inputlist, opts.output, opts.int_data_dir)

    if opts.dryrun:
        shellcmd = 'Locally in Bash'
        print '-'*5, shellcmd, '-'*(74 - len(shellcmd))
        print '$ cat /path/to/inputA /path/to/inputB',
        for name, j in joblist:
            if hasattr(j, 'map'):
                print '\\\n\t| %s %s %s %s | sort' % (opts.python, argv[0], _MAP, name),
            if hasattr(j, 'reduce'):
                print ' | %s %s %s %s' % (opts.python, argv[0], _RED, name),
        print
        for name, j in joblist:
            print '='*5, name, '='*(74 - len(name))
            print j.submit(argv, opts)
        print '='*80
    else:
        for name, j in joblist:
            print '='*5, name, '='*(74 - len(name))
            output = os.popen(j.submit(argv, opts))
            for line in output:
                print line,
            exitcode = output.close()
            print 'exitcode =', exitcode
            # TODO: exitcode == None implies 'success' but hadoop streaming
            # jobs exit w/ code 0 regardless.  We need some way of determining
            # failure.
            if None != exitcode:
                errmsg = 'Job failed (exit code %i).  Exiting...' % exitcode
                print >>sys.stderr, errmsg
                return 1
        print '='*80
    return 0


def getparser():
    '''Return the optparse.OptionParser instance used by run().

    Client programs can make adjustments to the default command line option
    processing configuraiton.  Modifying the option parser is recommended over
    parsing sys.argv and passing a modified version to run().
    '''
    return _parser


def _attr(klass, name, ifnotexists):
    if hasattr(klass, name): return getattr(klass, name)
    else: return ifnotexists


def getchild(klass): return _attr(klass, 'child', None)


def getrunner(klass): return _attr(klass, 'runner', PoopRunner())


def getstreamingjar(hadoophome):
    'Search for the Hadoop streaming jar within a Hadoop stack installation.'
    from subprocess import Popen, PIPE
    # -L flag for find is for the case where hadoophome is a symbolic link
    findcmd = ["find", '-L', hadoophome, '-name', '*streaming*jar']
    flist = Popen(findcmd, stdout=PIPE).communicate()[0].strip().splitlines()
    if len(flist) < 1:
        warning = 'WARNING! could not find the streaming jar in %s'
        print >>sys.stderr, warning % hadoophome
        print >>sys.stderr, 'You will not be able to run this command.'
        return 'JAR-NOT-FOUND'
    return flist[0].strip()


def makeklassdict(poopklass):
    '''Given a PoopJob class, build a dictionary of all associated job classes.
    The dict is keyed on class name.
    '''
    klassdict = {poopklass.__name__: poopklass}
    k = getchild(poopklass)
    while None != k:
        klassdict[k.__name__] = k
        k = getchild(k)
    return klassdict


def makejoblist(poopklass, input, output, int_data_dir='/__poop'):
    'Build a list of PoopJob *instances* with inputs and outputs filled in.'
    try:
        from hashlib import sha1    # 2.5
    except ImportError:
        from sha import new as sha1

    joblist = []
    joblist.append((poopklass.__name__, poopklass(None, input, output)))

    # we don't want intermediate ouputs for jobs with distinct final outputs to
    # collide so we sha1 the output and use that as part of intermediate file
    # names
    output_sha1 = sha1(output).hexdigest()

    i = 1
    while None != getchild(joblist[-1][1]):
        job = joblist[-1][1]
        # adjust output to go to an intermediate directory
        job.output = '/'.join((int_data_dir, output_sha1, str(i), job.name()))
        childklass = getchild(job)
        joblist.append((childklass.__name__, childklass(job, input, output)))
        i += 1

    return joblist