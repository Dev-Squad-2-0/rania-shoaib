import numpy as np

sensor = np.array([
     21, 22, 23, 24, 25,
    26, 27, 28, 29, 30,
    31, 32, 80, 33, 34])

print(sensor)

window = 3


# rolling mean 
rolling_mean = np.convolve(sensor, np.ones(window)/window, mode = 'valid')


# rolling std
rolling_std = np.array([np.std(sensor[i:i+window]) for i in range(len(sensor)-window+1)])


# Z-score normailzation 
mean = np.mean(sensor)
std = np.std(sensor)

zscores = (sensor - mean)/std

outliers = np.abs(zscores) > 2


print("\nRolling Mean: ", rolling_mean)
print("\nRolling std: ", rolling_std)

print("\nZ Scores:")
print(zscores)


print("\nOutliers: ", sensor[outliers])


