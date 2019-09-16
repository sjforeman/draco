"""Collection of RFI excision routines.
"""
import numpy as np
from scipy.ndimage import convolve1d

def sumthreshold_py(data, max_m=16, start_flag=None, threshold1=None, remove_median=True):
    
    m_min = 4  # Minimum value of m to reach
    data = np.copy(data)
    (ny, nx) = data.shape
    if start_flag is None:
        start_flag = np.isnan(data)
    flag = np.copy(start_flag)

    if remove_median:
        data -= np.median(data[~flag])

    if threshold1 is None:
        threshold1 = np.percentile(data[~flag], 95.)

    m=1
    while m <= max_m:
        if m==1:
            threshold = threshold1
        else:
            threshold = threshold1/1.5**(np.log2(m))
        data_rms = np.std(data[~flag])
        print(threshold, data_rms, threshold / data_rms)
#        if (m > m_min) and (threshold < 2.* data_rms):
#            break

        # The centre of the window for even windows is the bin right to the left of centre.
        # I want the origin at the leftmost bin
        if m==1:
            centre = 0
        else:
            centre = m//2 - 1
            
        # X-axis
        data[flag] = 0.
        count = (~flag).astype(np.float)
        # Convolution of the data
        dconv = convolve1d(data, weights=np.ones(m, dtype=float), origin=-centre, axis=1)[:, :(nx-m+1)]
        # Convolution of the counts
        cconv = convolve1d(count, weights=np.ones(m, dtype=float), origin=-centre, axis=1)[:, :(nx-m+1)]
        flag_temp = (dconv > cconv * threshold)
        flag_temp += (dconv < -cconv * threshold)
        for ii in range(flag_temp.shape[1]):
            flag[:, ii:ii+m] += flag_temp[:, ii][:, np.newaxis]
        
        # Y-axis
        data[flag] = 0.
        count = (~flag).astype(np.float)
        # Convolution of the data
        dconv = convolve1d(data, weights=np.ones(m, dtype=float), origin=-centre, axis=0)[:(ny-m+1), :]
        # Convolution of the counts
        cconv = convolve1d(count, weights=np.ones(m, dtype=float), origin=-centre, axis=0)[:(ny-m+1), :]
        flag_temp = (dconv > cconv * threshold)
        flag_temp += (dconv < -cconv * threshold)
        for ii in range(flag_temp.shape[0]):
            flag[ii:ii+m, :] += flag_temp[ii, :][np.newaxis, :]

        m *= 2

    return flag


# This routine might be substituted by a faster one later
sumthreshold = sumthreshold_py

