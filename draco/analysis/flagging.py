"""Tasks for flagging out bad or unwanted data.

This includes data quality flagging on timestream data; sun excision on sidereal
data; and pre-map making flagging on m-modes.

Tasks
=====

.. autosummary::
    :toctree:

    DayMask
    MaskData
    MaskBaselines
    RadiometerWeight
    SmoothVisWeight
    ThresholdVisWeight
    RFIMask
"""
# === Start Python 2/3 compatibility
from __future__ import absolute_import, division, print_function, unicode_literals
from future.builtins import *  # noqa  pylint: disable=W0401, W0614
from future.builtins.disabled import *  # noqa  pylint: disable=W0401, W0614

# === End Python 2/3 compatibility

import numpy as np
from scipy.ndimage import median_filter, uniform_filter

from caput import config, weighted_median
from ch_util import rfi, ephemeris
from ch_util.tools import invert_no_zero

from ..core import task, containers, io
from ..util import tools
from ..util import rfi as dracorfi


class DayMask(task.SingleTask):
    """Crudely simulate a masking out of the daytime data.

    Attributes
    ----------
    start, end : float
        Start and end of masked out region.
    width : float
        Use a smooth transition of given width between the fully masked and
        unmasked data. This is interior to the region marked by start and end.
    zero_data : bool, optional
        Zero the data in addition to modifying the noise weights
        (default is True).
    remove_average : bool, optional
        Estimate and remove the mean level from each visibilty. This estimate
        does not use data from the masked region.
    """

    start = config.Property(proptype=float, default=90.0)
    end = config.Property(proptype=float, default=270.0)

    width = config.Property(proptype=float, default=60.0)

    zero_data = config.Property(proptype=bool, default=True)
    remove_average = config.Property(proptype=bool, default=True)

    def process(self, sstream):
        """Apply a day time mask.

        Parameters
        ----------
        sstream : containers.SiderealStream
            Unmasked sidereal stack.

        Returns
        -------
        mstream : containers.SiderealStream
            Masked sidereal stream.
        """

        sstream.redistribute("freq")

        ra_shift = (sstream.ra[:] - self.start) % 360.0
        end_shift = (self.end - self.start) % 360.0

        # Crudely mask the on and off regions
        mask_bool = ra_shift > end_shift

        # Put in the transition at the start of the day
        mask = np.where(
            ra_shift < self.width,
            0.5 * (1 + np.cos(np.pi * (ra_shift / self.width))),
            mask_bool,
        )

        # Put the transition at the end of the day
        mask = np.where(
            np.logical_and(ra_shift > end_shift - self.width, ra_shift <= end_shift),
            0.5 * (1 + np.cos(np.pi * ((ra_shift - end_shift) / self.width))),
            mask,
        )

        if self.remove_average:
            # Estimate the mean level from unmasked data
            import scipy.stats

            nanvis = (
                sstream.vis[:]
                * np.where(mask_bool, 1.0, np.nan)[np.newaxis, np.newaxis, :]
            )
            average = scipy.stats.nanmedian(nanvis, axis=-1)[:, :, np.newaxis]
            sstream.vis[:] -= average

        # Apply the mask to the data
        if self.zero_data:
            sstream.vis[:] *= mask

        # Modify the noise weights
        sstream.weight[:] *= mask ** 2

        return sstream


class MaskData(task.SingleTask):
    """Mask out data ahead of map making.

    Attributes
    ----------
    auto_correlations : bool
        Exclude auto correlations if set (default=False).
    m_zero : bool
        Ignore the m=0 mode (default=False).
    positive_m : bool
        Include positive m-modes (default=True).
    negative_m : bool
        Include negative m-modes (default=True).
    """

    auto_correlations = config.Property(proptype=bool, default=False)
    m_zero = config.Property(proptype=bool, default=False)
    positive_m = config.Property(proptype=bool, default=True)
    negative_m = config.Property(proptype=bool, default=True)

    def process(self, mmodes):
        """Mask out unwanted datain the m-modes.

        Parameters
        ----------
        mmodes : containers.MModes

        Returns
        -------
        mmodes : containers.MModes
        """

        # Exclude auto correlations if set
        if not self.auto_correlations:
            for pi, (fi, fj) in enumerate(mmodes.index_map["prod"]):
                if fi == fj:
                    mmodes.weight[..., pi] = 0.0

        # Apply m based masks
        if not self.m_zero:
            mmodes.weight[0] = 0.0

        if not self.positive_m:
            mmodes.weight[1:, 0] = 0.0

        if not self.negative_m:
            mmodes.weight[1:, 1] = 0.0

        return mmodes


class MaskBaselines(task.SingleTask):
    """Mask out baselines from a dataset.

    Attributes
    ----------
    mask_long_ns : float
        Mask out baselines longer than a given distance in the N/S direction.
    mask_short : float
        Mask out baselines shorter than a given distance.
    mask_short_ew : float
        Mask out baselines shorter then a given distance in the East-West
        direction. Usefull for masking out intra-cylinder baselines for
        North-South oriented cylindrical telescopes.
    zero_data : bool, optional
        Zero the data in addition to modifying the noise weights
        (default is False).
    """

    mask_long_ns = config.Property(proptype=float, default=None)
    mask_short = config.Property(proptype=float, default=None)
    mask_short_ew = config.Property(proptype=float, default=None)

    zero_data = config.Property(proptype=bool, default=False)

    def setup(self, telescope):
        """Set the telescope model.

        Parameters
        ----------
        telescope : TransitTelescope
        """

        self.telescope = io.get_telescope(telescope)

    def process(self, ss):
        """Apply the mask to data.

        Parameters
        ----------
        ss : SiderealStream or TimeStream
            Data to mask. Applied in place.
        """

        ss.redistribute("freq")

        baselines = self.telescope.baselines
        mask = np.ones_like(ss.weight[:], dtype=bool)

        if self.mask_long_ns is not None:
            long_ns_mask = np.abs(baselines[:, 1]) < self.mask_long_ns
            mask *= long_ns_mask[np.newaxis, :, np.newaxis]

        if self.mask_short is not None:
            short_mask = np.sum(baselines ** 2, axis=1) > self.mask_short
            mask *= short_mask[np.newaxis, :, np.newaxis]

        if self.mask_short_ew is not None:
            short_ew_mask = baselines[:, 0] > self.mask_short_ew
            mask *= short_ew_mask[np.newaxis, :, np.newaxis]

        # Apply the mask to the weight
        ss.weight[:] *= mask
        # Apply the mask to the data
        if self.zero_data:
            ss.vis[:] *= mask

        return ss


class RadiometerWeight(task.SingleTask):
    """Update vis_weight according to the radiometer equation:

    .. math::

        \text{weight}_{ij} = N_\text{samp} / V_{ii} V_{jj}

    Attributes
    ----------
    replace : bool, optional
        Replace any existing weights (default). If `False` then we multiply the
        existing weights by the radiometer values.

    """

    replace = config.Property(proptype=bool, default=True)

    def process(self, stream):
        """Change the vis weight.

        Parameters
        ----------
        stream : SiderealStream or TimeStream
            Data to be weighted. This is done in place.

        Returns
        --------
        stream : SiderealStream or TimeStream
        """

        from caput.time import STELLAR_S

        # Redistribute over the frequency direction
        stream.redistribute("freq")

        ninput = len(stream.index_map["input"])
        nprod = len(stream.index_map["prod"])

        if nprod != (ninput * (ninput + 1) // 2):
            raise RuntimeError(
                "Must have a input stream with the full correlation triangle."
            )

        freq_width = np.median(stream.index_map["freq"]["width"])

        if isinstance(stream, containers.SiderealStream):
            RA_S = 240 * STELLAR_S  # SI seconds in 1 deg of RA change
            int_time = np.median(np.abs(np.diff(stream.ra))) / RA_S
        else:
            int_time = np.median(np.abs(np.diff(stream.index_map["time"])))

        if self.replace:
            stream.weight[:] = 1.0

        # Construct and set the correct weights in place
        nsamp = 1e6 * freq_width * int_time
        autos = tools.extract_diagonal(stream.vis[:]).real
        weight_fac = nsamp ** 0.5 / autos
        tools.apply_gain(stream.weight[:], weight_fac, out=stream.weight[:])

        # Return timestream with updated weights
        return stream


class SmoothVisWeight(task.SingleTask):
    """Smooth the visibility weights with a median filter.

    This is done in-place.

    Attributes
    ----------
    kernel_size : int
        Size of the kernel for the median filter in time points.
        Default is 31, corresponding to ~5 minutes window for 10s cadence data.

    """

    # 31 time points correspond to ~ 5min in 10s cadence
    kernel_size = config.Property(proptype=int, default=31)

    def process(self, data):
        """Smooth the weights with a median filter.

        Parameters
        ----------
        data : :class:`andata.CorrData` or :class:`containers.TimeStream` object
            Data containing the weights to be smoothed

        Returns
        -------
        data : Same object as data
            Data object containing the same data as the input, but with the
            weights substituted by the smoothed ones.
        """

        # Ensure data is distributed in frequency:
        data.redistribute("freq")
        # Full slice reutrns an MPIArray
        weight = data.weight[:]
        # Data will be distributed in frequency.
        # So a frequency loop will not be too large.
        for lfi, gfi in weight.enumerate(axis=0):

            # MPIArray takes the local index, returns a local np.ndarray
            # Find values equal to zero to preserve them in final weights
            zeromask = weight[lfi] == 0.0
            # Median filter. Mode='nearest' to prevent steps close to
            # the end from being washed
            weight[lfi] = median_filter(
                weight[lfi], size=(1, self.kernel_size), mode="nearest"
            )
            # Ensure zero values are zero
            weight[lfi][zeromask] = 0.0

        return data


class ThresholdVisWeight(task.SingleTask):
    """Set any weight less than the user specified threshold equal to zero.

    Threshold is determined as `maximum(absolute_threshold, relative_threshold * mean(weight))`.

    Parameters
    ----------
    absolute_threshold : float
        Any weights with values less than this number will be set to zero.
    relative_threshold : float
        Any weights with values less than this number times the average weight
        will be set to zero.
    """

    absolute_threshold = config.Property(proptype=float, default=1e-7)
    relative_threshold = config.Property(proptype=float, default=0.0)

    def process(self, timestream):
        """Apply threshold to `weight` dataset.

        Parameters
        ----------
        timestream : `.core.container` with `weight` attribute

        Returns
        -------
        timestream : same as input timestream
            The input container with modified weights.
        """
        weight = timestream.weight[:]

        threshold = self.absolute_threshold
        if self.relative_threshold > 0.0:
            sum_weight = self.comm.allreduce(np.sum(weight))
            mean_weight = sum_weight / float(np.prod(weight.global_shape))
            threshold = np.maximum(threshold, self.relative_threshold * mean_weight)

        keep = weight > threshold

        self.log.info(
            "%0.5f%% of data is below the weight threshold of %0.1e."
            % (100.0 * (1.0 - np.sum(keep) / float(keep.size)), threshold)
        )

        timestream.weight[:] = np.where(keep, weight, 0.0)

        return timestream


class RFISensitivityMask(task.SingleTask):
    """Slightly less crappy RFI masking.

    Attributes
    ----------
    sigma : float, optional
        The false positive rate of the flagger given as sigma value assuming
        the non-RFI samples are Gaussian.
        Used for the MAD and TV station flaggers.
    tv_fraction : float, optional
        Number of bad samples in a digital TV channel that cause the whole
        channel to be flagged.
    max_m : int, optional
        Maximum size of the Sumthreshold window to use.
        The default (8) seems to work well with sensitivity data.
    threshold_sigma : float, optional
        The desired threshold for the Sumthreshold algorythm at the
        final window size (determined by max m) given as a 
        number of standard deviations (to be estimated from the 
        sensitivity map excluding weight and static masks).
        The default (8) seems to work well with sensitivity data
        using the default max_m.
    remove_median : bool, optional
        Remove median accross times for each frequency?
        Recomended. Default: True.
    sir : bool, optional
        Apply scale invariant rank (SIR) operator on top of final mask?
        We find that this is advisable while we still haven't flagged 
        out all the static bands properly. Default: True.
    mask_type : string, optional
        One of 'mad', 'sumthreshold' or 'combine'.
        Default is combine, which uses the sumthreshold everywhere
        except around the transits of the Sun, CasA and CygA where it
        applies the MAD mask to avoid masking out the transits.
    """

    sigma = config.Property(proptype=float, default=5.0)
    tv_fraction = config.Property(proptype=float, default=0.5)
    max_m = config.Property(proptype=int, default=8)
    start_threshold_sigma = config.Property(proptype=float, default=8)
    remove_median = config.Property(proptype=bool, default=True)
    sir = config.Property(proptype=bool, default=True)
    mask_type = config.enum(["mad", "sumthreshold", "combine"], default="combine")

    MADTORMS = 1.4826  # Conver MAD to RMS
    # The difference between the exponents in the usual
    # scaling of the RMS (n**0.5) and the scaling used
    # in the sumthreshold algorythm (n**log2(1.5))
    RMS_SCALING_DIFF = np.log2(1.5) - 0.5

    def process(self, sensitivity):
        """Derive an RFI mask from sensitivity data.

        Parameters
        ----------
        sensitivity : containers.SystemSensitivity
            Sensitivity data to derive the RFI mask from.

        Returns
        -------
        rfimask : containers.RFIMask
            RFI mask derived from sensitivity.
        """
        # Divide sensitivity to get a radiometer test
        radiometer = sensitivity.sigma[:] * invert_no_zero(sensitivity.estimate[:])
        npol = radiometer.shape[1]
        nfreq = radiometer.shape[0]
        freq = sensitivity.freq
        static_flag = rfi.frequency_mask(freq)

        madmask, stmask = [], []
        for ii in range(npol):
            # Initial flag on weights equal to zero.
            origflag = sensitivity.weight[:, ii] == 0.0
            # Remove median at each frequency, if asked.
            if self.remove_median:
                for ff in range(nfreq):
                    radiometer[ff, ii] -= np.median(radiometer[ff, ii][~origflag[ff]])
            # Combine weights with static flag
            start_flag = origflag | static_flag[:, None]
            # Obtain MAD and TV masks
            this_madmask, tvmask = self.mad_tv_mask(
                radiometer[:, ii],
                start_flag,
                freq,
                sigma=self.sigma,
                tv_fraction=self.tv_fraction,
            )
            # combine MAD and TV masks
            madmask.append(this_madmask | tvmask)

            # Add TV channels to ST start flag.
            start_flag = start_flag | tvmask
            # Determine initial threshold
            med = np.median(radiometer[:, ii][~start_flag])
            mad = np.median(abs(radiometer[:, ii][~start_flag] - med))
            rms = self.MADTORMS * mad
            threshold1 = (
                rms * self.start_threshold_sigma * self.max_m ** self.RMS_SCALING_DIFF
            )

            # SUMTHRESHOLD mask
            stmask.append(
                dracorfi.sumthreshold(
                    radiometer[:, ii],
                    self.max_m,
                    start_flag=start_flag,
                    threshold1=threshold1,
                    correct_for_missing=True,
                )
            )

        # Combine two polarization flags
        madmask = np.logical_or(madmask[0], madmask[1])
        stmask = np.logical_or(stmask[0], stmask[1])

        if self.mask_type == "mad":
            finalmask = madmask
        elif self.mask_type == "sumthreshold":
            finalmask = stmask
        else:  # Combine ST and MAD masks
            sidereal_day = ephemeris.SIDEREAL_S * 24 * 3600
            timestamp = sensitivity.time
            ntm = len(timestamp)
            start_csd = ephemeris.unix_to_csd(timestamp[0])
            start_ra = (start_csd - int(start_csd)) * (2.0 * np.pi)
            ra_axis = (start_ra + (2.0 * np.pi) / ntm * np.arange(ntm)) % (2.0 * np.pi)
            # Select Sun transit times
            suntt = ephemeris.solar_transit(timestamp[0])  # Sun transit time
            sunidx = np.argmin(abs(timestamp - suntt))
            beam_window = (
                2.0 * np.pi
            ) / 90.0  # 4 deg in radians for the Sun. Each side.
            # Mask the sun for the maximum aparent dec of ~24 deg
            ra_window = beam_window / np.cos(np.deg2rad(24))
            time_window = ra_window / (2 * np.pi) * sidereal_day
            # Time spans where we apply the MAD filter.
            madtimes = abs(timestamp - suntt) < time_window
            # Select bright source transit times. Only CasA, CygA and TauA.
            sources = [ephemeris.CasA, ephemeris.CygA, ephemeris.TauA]
            # , ephemeris.VirA]
            for src in sources:
                src_ra = src.ra.radians
                src_idx = np.argmin(abs(ra_axis - src_ra))
                ra_window = beam_window / np.cos(src.dec.radians)
                time_window = ra_window / (2 * np.pi) * sidereal_day
                # Include bright point source transit in MAD times
                madtimes += abs(timestamp - timestamp[src_idx]) < time_window
            # Create final mask
            finalmask = stmask
            finalmask[:, madtimes] = madmask[:, madtimes]

        # Apply scale invariant rank (SIR) operator, if asked for.
        if self.sir:
            finalmask = self.add_sir(finalmask, static_flag)

        # Create container to hold mask
        rfimask = containers.RFIMask(axes_from=sensitivity)
        rfimask.mask[:] = finalmask

        return rfimask

    def add_sir(self, mask, baseflag, eta=0.2):
        # Remove baseflag from mask and run SIR
        nobaseflag = np.copy(mask)
        nobaseflag[baseflag] = False
        nobaseflagsir = rfi.sir(nobaseflag[:, np.newaxis, :], eta=eta)[:, 0, :]
        # Make sure the original mask (including baseflag) is still masked
        flagsir = nobaseflagsir | mask
        return flagsir

    def mad_tv_mask(
        self,
        data,
        start_flag,
        freq,
        sigma=5.0,
        tv_fraction=0.5,
        base_size=(11, 3),
        mad_size=(201, 51),
    ):
        # Make copy of data
        data = np.copy(data)
        # Calculate the scaled deviations
        data[start_flag] = 0.0
        maddev = mad(data, start_flag, base_size=base_size, mad_size=mad_size)
        # Replace any NaNs (where too much data is missing) with a
        # large enough value to always be flagged
        maddev = np.where(np.isnan(maddev), 2 * sigma, maddev)
        # Reflag for scattered TV emission
        tvmask = tv_channels_flag(maddev, freq, sigma=sigma, f=tv_fraction)
        # Create MAD mask
        madmask = maddev > sigma
        # Ensure start flag is masked
        madmask = madmask | start_flag

        return madmask, tvmask


class RFIMask(task.SingleTask):
    """Crappy RFI masking.

    Attributes
    ----------
    sigma : float, optional
        The false positive rate of the flagger given as sigma value assuming
        the non-RFI samples are Gaussian.
    tv_fraction : float, optional
        Number of bad samples in a digital TV channel that cause the whole
        channel to be flagged.
    stack_ind : int
        Which stack to process to derive flags for the whole dataset.
    destripe : bool, optional
        Deprecated option to remove the striping.
    """

    sigma = config.Property(proptype=float, default=5.0)
    tv_fraction = config.Property(proptype=float, default=0.5)
    stack_ind = config.Property(proptype=int)
    destripe = config.Property(proptype=bool, default=False)

    def process(self, sstream):
        """Apply a day time mask.

        Parameters
        ----------
        sstream : containers.SiderealStream
            Unmasked sidereal stack.

        Returns
        -------
        mstream : containers.SiderealStream
            Masked sidereal stream.
        """

        sstream.redistribute("stack")

        ssv = sstream.vis[:]
        ssw = sstream.weight[:]

        # Figure out which rank actually has the requested index
        lstart = ssv.local_offset[1]
        lstop = lstart + ssv.local_shape[1]
        has_ind = (self.stack_ind >= lstart) and (self.stack_ind < lstop)
        has_ind_list = sstream.comm.allgather(has_ind)
        rank_with_ind = has_ind_list.index(True)
        self.log.debug(
            "Rank %i has the requested index %i", rank_with_ind, self.stack_ind
        )

        newmask = np.zeros((ssv.shape[0], ssv.shape[2]), dtype=np.bool)

        # Get the rank with stack to create the new mask
        if sstream.comm.rank == rank_with_ind:

            # Cut out the right section
            wf = ssv[:, self.stack_ind - lstart].view(np.ndarray)
            ww = ssw[:, self.stack_ind - lstart].view(np.ndarray)

            # Generate an initial mask and calculate the scaled deviations
            # TODO: replace this magic threshold
            weight_cut = 1e-4 * ww.mean()  # Ignore samples with small weights
            wm = ww < weight_cut
            maddev = mad(wf, wm)

            # Replace any NaNs (where too much data is missing) with a large enough value to always
            # be flagged
            maddev = np.where(np.isnan(maddev), 2 * self.sigma, maddev)

            # Reflag for scattered TV emission
            tvmask = tv_channels_flag(
                maddev, sstream.freq, sigma=self.sigma, f=self.tv_fraction
            )

            # Construct the new mask
            newmask[:] = tvmask | (maddev > self.sigma)

        # Broadcat the new flags to all ranks and then apply
        sstream.comm.Bcast(newmask, root=rank_with_ind)
        ssw[:] *= (~newmask)[:, np.newaxis, :]

        self.log.info(
            "Flagging %0.2f%% of data due to RFI."
            % (100.0 * np.sum(newmask) / float(newmask.size))
        )

        # Remove the time average of the data. Should probably do this elsewhere to be honest
        if self.destripe:
            self.log.info("Destriping the data. This option is deprecated.")
            weight_cut = 1e-4 * ssw.mean()  # Ignore samples with small weights
            ssv[:] = destripe(ssv, ssw > weight_cut)

        return sstream


def medfilt(x, mask, size, *args):
    """Apply a moving median filter to masked data.

    The application is done by iterative filling to
    overcome the fact we don't have an actual implementation
    of a nanmedian.

    Parameters
    ----------
    x : np.ndarray
        Data to filter.
    mask : np.ndarray
        Mask of data to filter out.
    size : tuple
        Size of the window in each dimension.

    Returns
    -------
    y : np.ndarray
        The masked data. Data within the mask is undefined.
    """

    if np.iscomplexobj(x):
        return medfilt(x.real, mask, size) + 1.0j * medfilt(x.imag, mask, size)

    # Copy and do initial masking
    x = np.ascontiguousarray(x.astype(np.float64))
    w = np.ascontiguousarray((~mask).astype(np.float64))

    return weighted_median.moving_weighted_median(x, w, size, *args)


def mad(x, mask, base_size=(11, 3), mad_size=(21, 21), debug=False, sigma=True):
    """Calculate the MAD of freq-time data.

    Parameters
    ----------
    x : np.ndarray
        Data to filter.
    mask : np.ndarray
        Initial mask.
    base_size : tuple
        Size of the window to use in (freq, time) when
        estimating the baseline.
    mad_size : tuple
        Size of the window to use in (freq, time) when
        estimating the MAD.
    sigma : bool, optional
        Rescale the output into units of Gaussian sigmas.

    Returns
    -------
    mad : np.ndarray
        Size of deviation at each point in MAD units.
    """

    xs = medfilt(x, mask, size=base_size)
    dev = np.abs(x - xs)

    mad = medfilt(dev, mask, size=mad_size)

    if sigma:
        mad *= 1.4826  # apply the conversion from MAD->sigma

    if debug:
        return dev / mad, dev, mad

    return dev / mad


def inverse_binom_cdf_prob(k, N, F):
    """Calculate the trial probability that gives the CDF.

    This gets the trial probability that gives an overall cumulative
    probability for Pr(X <= k; N, p) = F

    Parameters
    ----------
    k : int
        Maximum number of successes.
    N : int
        Total number of trials.
    F : float
        The cumulative probability for (k, N).

    Returns
    -------
    p : float
        The trial probability.
    """
    # This uses the result that we can write the cumulative probability of a
    # binomial in terms of an incomplete beta function

    import scipy.special as sp

    return sp.betaincinv(k + 1, N - k, 1 - F)


def sigma_to_p(sigma):
    """Get the probability of an excursion larger than sigma for a Gaussian."""
    import scipy.stats as ss

    return 2 * ss.norm.sf(sigma)


def p_to_sigma(p):
    """Get the sigma exceeded by the tails of a Gaussian with probability p."""
    import scipy.stats as ss

    return ss.norm.isf(p / 2)


def tv_channels_flag(x, freq, sigma=5, f=0.5, debug=False):
    """Perform a higher sensitivity flagging for the TV stations.

    This flags a whole TV station band if more than fraction f of the samples
    within a station band exceed a given threshold. The threshold is calculated
    by wanting a fixed false positive rate (as described by sigma) for fraction
    f of samples exceeding the threshold

    Parameters
    ----------
    x : np.ndarray[freq, time]
        Deviations of data in sigma units.
    freq : np.ndarray[freq]
        Frequency of samples in MHz.
    sigma : float, optional
        The probability of a false positive given as a sigma of a Gaussian.
    f : float, optional
        Fraction of bad samples within each channel before flagging the whole
        thing.
    debug : bool, optional
        Returns (mask, fraction) instead to give extra debugging info.

    Returns
    -------
    mask : np.ndarray[bool]
        Mask of the input data.
    """

    p_false = sigma_to_p(sigma)
    frac = np.ones_like(x, dtype=np.float)

    tvstart_freq = 398
    tvwidth_freq = 6

    for i in range(67):

        fs = tvstart_freq + i * tvwidth_freq
        fe = fs + tvwidth_freq
        sel = (freq >= fs) & (freq < fe)

        # Calculate the threshold to apply
        N = sel.sum()
        k = int(f * N)

        # This is the Gaussian threshold required for there to be at most a p_false chance of more
        # than k trials exceeding the threshold. This is the correct expression, and has been double
        # checked by numerical trials.
        t = p_to_sigma(inverse_binom_cdf_prob(k, N, 1 - p_false))

        frac[sel] = (x[sel] > t).mean(axis=0)[np.newaxis, :]

    mask = frac > f

    if debug:
        return mask, frac

    return mask


def complex_med(x, *args, **kwargs):
    """Complex median, done by applying to the real/imag parts individually.

    Parameters
    ----------
    x : np.ndarray
        Array to apply to.
    *args, **kwargs : list, dict
        Passed straight through to `np.nanmedian`

    Returns
    -------
    m : np.ndarray
        Median.
    """
    return np.nanmedian(x.real, *args, **kwargs) + 1j * np.nanmedian(
        x.imag, *args, **kwargs
    )


def destripe(x, w, axis=1):
    """Subtract the median along a specified axis.

    Parameters
    ----------
    x : np.ndarray
        Array to destripe.
    w : np.ndarray
        Mask array for points to include (True) or ignore (False).
    axis : int, optional
        Axis to apply destriping along.

    Returns
    -------
    y : np.ndarray
        Destriped array.
    """

    # Calculate the average along the axis
    stripe = complex_med(np.where(w, x, np.nan), axis=axis)
    stripe = np.nan_to_num(stripe)

    # Construct a slice to broadcast back along the axis
    bsel = [slice(None)] * x.ndim
    bsel[axis] = None
    bsel = tuple(bsel)

    return x - stripe[bsel]
