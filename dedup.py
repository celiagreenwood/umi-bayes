#!/usr/bin/env python

import collections, argparse, pysam, sys
from lib import parse_sam, umi_data, optical_duplicates, naive_estimate

# parse arguments
parser = argparse.ArgumentParser(description = 'Read a coordinate-sorted BAM file with labeled UMIs and mark or remove duplicates due to PCR or optical cloning, but not duplicates present in the original library. When PCR/optical duplicates are detected, the reads with the highest total base qualities are marked as non-duplicate - note we do not discriminate on MAPQ, or other alignment features, because this would bias against polymorphisms.')
parser.add_argument('-r', '--remove', action = 'store_true', help = 'remove PCR/optical duplicates instead of marking them')
parser.add_argument('-d', '--dist', action = 'store', help = 'maximum pixel distance for optical duplicates (Euclidean); set to 0 to skip optical duplicate detection', type = int, default = 100)
parser.add_argument('-u', '--umi_table', action = 'store', help = 'table of UMI sequences and prior frequencies')
parser.add_argument('in_file', action = 'store', nargs = '?', default = '-')
parser.add_argument('out_file', action = 'store', nargs = '?', default = '-')
args = parser.parse_args()
if args.umi_table is None and args.in_file == '-':
	raise RuntimeError('you must provide a UMI table filename, a BAM filename, or both')

in_bam = pysam.Samfile(args.in_file, 'rb')
if in_bam.header['HD'].get('SO') != 'coordinate': raise RuntimeError('input file must be sorted by coordinate')
out_bam = pysam.Samfile(args.out_file, 'wb', template = in_bam) # should add a line to the header indicating it was processed
read_counter = collections.Counter()

'''
central concept: cheat a little, detecting duplicate reads by the fact that they align to the same (start) position rather than by their sequences
implementation: as you traverse the coordinate-sorted input reads, add each read to both a FIFO buffer (so they can be output in the same order) and a dictionary that groups reads by start position and strand (which is what you need for deduplication); this is not inefficient because both data structures contain pointers to the same pysam.AlignedSegment objects
then, after each input read, check the left end of the buffer to tell whether the oldest read is in a position that will never accumulate any more hits (because the input is sorted by coordinate); if so, estimate the duplication at that position, mark all the reads there accordingly, then output the read with the appropriate marking
'''


read_buffer = collections.deque()
pos_tracker = ({}, {}) # data structure containing observed UMIs and corresponding tracking information; top level is by strand (0 = forward, 1 = reverse), then next level is by 5' read start position (dict since these will be sparse and are only looked up by identity), then at each position the next level is by UMI (dict), and that contains a variety of data; there is no level for reference ID because there is no reason to store more than one chromosome at a time
def pop_buffer(): # pop the oldest read off the buffer (into the output), but first make sure its position has been deduplicated
	read = read_buffer.popleft()
	start_pos, umi = parse_sam.get_start_pos(read), umi_data.get_umi(read.query_name)
	this_pos = pos_tracker[read.is_reverse][start_pos]
	
	# deduplicate reads at this position
	if not this_pos['deduplicated']:
		umi_reads = this_pos['reads']
		
		# first pass: mark optical duplicates
		if args.dist != 0:
			for reads in umi_reads.values():
				for opt_dup in optical_duplicates.get_optical_duplicates(reads, args.dist):
					for read in umi_data.mark_duplicates(opt_dup, len(opt_dup) - 1):
						if read.is_duplicate: reads.remove(read) # remove duplicate reads from the tracker so they won't be considered later (they're still in the read buffer)
					read_counter['optical duplicate'] += len(opt_dup) - 1
		
		# second pass: mark PCR duplicates
		umi_counts = umi_data.make_umi_counts(umi_totals.keys())
		for umi, reads in umi_reads.items(): umi_counts[umi] = len(reads)
		
		# P ESTIMATION / DEDUPLICATION GOES HERE
		dedup_counts = naive_estimate.deduplicate_counts(umi_counts)
		
		for umi, reads in umi_reads.items():
			n_dup = len(reads) - dedup_counts[umi]
			umi_data.mark_duplicates(reads, n_dup)
			read_counter['PCR duplicate'] += n_dup
		
		read_counter['unique'] += sum(dedup_counts.values())

		this_pos['deduplicated'] = True
	
	# output read
	if not (args.remove and read.is_duplicate): out_bam.write(read)
	
	# prune the tracker
	if read is this_pos['last read']: del pos_tracker[read.is_reverse][start_pos]


# first pass through the input: get total UMI counts (or use table instead, if provided)
try:
	umi_totals = umi_data.read_umi_counts_from_table(open(args.umi_table))
except TypeError:
	umi_totals = umi_data.read_umi_counts_from_reads(in_bam)
	sys.stderr.write('%i\tusable alignments read\n' % sum(umi_totals.values()))
	in_bam.reset()


# second pass through the input
for read in in_bam:
	if not parse_sam.read_is_good(read): continue
	umi = umi_data.get_umi(read.query_name)
	if not umi_data.umi_is_good(umi): continue
	read.is_duplicate = False # not sure how to handle reads that have already been deduplicated somehow, so just ignore previous annotations
	start_pos = parse_sam.get_start_pos(read)
	read_counter['read'] += 1
	
	# advance the buffer
	while read_buffer and (read_buffer[0].reference_id < read.reference_id or parse_sam.get_start_pos(read_buffer[0]) < read.reference_start): pop_buffer() # pop the top read if it's at a position that's definitely not going to get any more hits
	
	# add read to buffer and tracking data structure	
	read_buffer.extend([read])
	try:
		pos_tracker[read.is_reverse][start_pos]['reads'][umi] += [read]
	except KeyError: # first time we've seen this UMI at this position+strand
		try:
			pos_tracker[read.is_reverse][start_pos]['reads'][umi] = [read]
		except KeyError: # first time we've since this position+strand
			pos_tracker[read.is_reverse][start_pos] = {'reads': {umi: [read]}, 'deduplicated': False}
	pos_tracker[read.is_reverse][start_pos]['last read'] = read

# flush the buffer
while read_buffer: pop_buffer()


# generate summary statistics
if args.umi_table is None:
	assert sum(umi_totals.values()) == read_counter['read']
else:
	sys.stderr.write('%i\tusable alignments read\n' % read_counter['read'])
if args.dist != 0: sys.stderr.write('%i\toptical duplicate\n' % read_counter['optical duplicate'])
sys.stderr.write('%i\tPCR duplicate\n%i\tunique\n' % (read_counter['PCR duplicate'], read_counter['unique']))

